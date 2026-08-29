"""Background automation bot for the Android game "The Tower".

Everything runs through ADB, so the emulator window never needs focus and the
mouse is never hijacked:

    screen capture  ->  device.screenshot()     (PIL image, converted in memory)
    template match  ->  cv2.matchTemplate
    click           ->  device.click(x, y)       (an `input tap` under the hood)

Usage:
    python tower_bot.py                 # run the loop
    python tower_bot.py --once          # single scan, useful while tuning
    python tower_bot.py --debug-scores  # raise the log level to DEBUG
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from types import FrameType

from adbutils import AdbDevice

import config
import events
import screens
import vision
from affordability import AffordabilityCheck, BrightnessAffordability
from device import EmulatorError, Image, capture_screen, connect_device, tap
from sinks.log import LogSink

logger = logging.getLogger("tower_bot")


# --------------------------------------------------------------------------
# Bot
# --------------------------------------------------------------------------
class TowerBot:
    def __init__(
        self,
        device: AdbDevice,
        templates: vision.TemplateCache,
        bus: events.EventBus,
        click_cooldown: float = config.CLICK_COOLDOWN_SECONDS,
        affordability_check: AffordabilityCheck | None = None,
    ) -> None:
        self.device = device
        self.templates = templates
        self.bus = bus
        self.click_cooldown = click_cooldown
        self.affordability: AffordabilityCheck = affordability_check or BrightnessAffordability()
        self.tracker = screens.ScreenTracker()
        self._screen: Image | None = None
        self._last_click: dict[str, float] = {}
        self._running = True

    @property
    def screen_state(self) -> screens.ScreenState:
        return self.tracker.state

    # -- screen state ------------------------------------------------------
    def refresh_screen(self) -> Image:
        """Capture a fresh frame and keep it as the current screen."""
        self._screen = capture_screen(self.device)
        return self._screen

    @property
    def screen(self) -> Image:
        if self._screen is None:
            return self.refresh_screen()
        return self._screen

    # -- the core helper ---------------------------------------------------
    def find_and_click_image(
        self,
        template_path: str | Path,
        threshold: float = config.DEFAULT_THRESHOLD,
    ) -> bool:
        """Find the template on the current screen and tap it.

        Rejects are ordered cheapest-first, so a non-IN_RUN scan costs almost
        nothing.
        """
        key = str(template_path)

        if self.tracker.state is not screens.ScreenState.IN_RUN:
            self.bus.publish(
                events.Skipped(
                    action=key,
                    reason="screen_gated",
                    detail=f"screen is {self.tracker.state.value}",
                )
            )
            return False

        template = self.templates.get(template_path)
        match = vision.locate_template(self.screen, template, threshold)
        if match is None:
            return False

        ok, detail = self.affordability.affordable(self.screen, match, template)
        if not ok:
            self.bus.publish(
                events.Skipped(action=key, reason="dimmed", detail=detail)
            )
            return False

        now = time.monotonic()
        if now - self._last_click.get(key, 0.0) < self.click_cooldown:
            self.bus.publish(events.Skipped(action=key, reason="cooldown"))
            return False

        x, y = match.center
        tap(self.device, x, y)
        self._last_click[key] = now
        self.bus.publish(events.Tapped(action=key, x=x, y=y, score=match.score))
        return True

    # -- main loop ---------------------------------------------------------
    def run_once(self) -> bool:
        """One scan pass over the configured actions. True if anything clicked."""
        started = time.monotonic()
        self.refresh_screen()

        reading = screens.classify(self.screen, self.templates)
        previous = self.tracker.state
        if self.tracker.observe(reading) is not None:
            self.bus.publish(
                events.ScreenChanged(
                    prev=previous.value,
                    curr=self.tracker.state.value,
                    confidence=reading.confidence,
                    scores=reading.scores,
                )
            )

        clicked = False
        for action in config.ACTIONS:
            if self.find_and_click_image(action.template, action.threshold):
                clicked = True

        self.bus.publish(
            events.ScanCompleted(
                screen=self.tracker.state.value,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        )
        return clicked

    def run_forever(self, interval: float = config.SCAN_INTERVAL_SECONDS) -> None:
        logger.info("Bot started - scanning every %.1fs. Ctrl+C to stop.", interval)
        while self._running:
            try:
                self.run_once()
            except EmulatorError as exc:
                logger.error("Device error: %s - retrying in %.1fs", exc, interval)
            except Exception:  # noqa: BLE001 - never let one bad frame kill the loop
                logger.exception("Unexpected error during scan")
            time.sleep(interval)
        logger.info("Bot stopped.")

    def stop(self, *_: object) -> None:
        self._running = False


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Background bot for The Tower.")
    parser.add_argument("--host", default=config.DEVICE_HOST, help="emulator ADB host")
    parser.add_argument("--port", type=int, default=config.DEVICE_PORT, help="emulator ADB port")
    parser.add_argument(
        "--interval", type=float, default=config.SCAN_INTERVAL_SECONDS,
        help="seconds between scans",
    )
    parser.add_argument("--once", action="store_true", help="run a single scan and exit")
    parser.add_argument(
        "--debug-scores", action="store_true",
        help="raise the log level to DEBUG (shows the dimmed/cooldown skip reasons)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug_scores else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        device = connect_device(host=args.host, port=args.port)
    except EmulatorError as exc:
        logger.error("%s", exc)
        return 1

    bus = events.EventBus()
    log_sink = LogSink()
    log_sink.start()
    bus.subscribe(log_sink)

    bot = TowerBot(device=device, templates=vision.TemplateCache(config.TEMPLATE_DIR), bus=bus)

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s - shutting down after this scan.", signum)
        bot.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        if args.once:
            bot.run_once()
        else:
            bot.run_forever(interval=args.interval)
    finally:
        log_sink.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
