"""Background automation bot for the Android game "The Tower".

Everything runs through ADB, so the emulator window never needs focus and the
mouse is never hijacked:

    screen capture  ->  device.screenshot()     (PIL image, converted in memory)
    template match  ->  cv2.matchTemplate
    click           ->  device.click(x, y)       (an `input tap` under the hood)

Usage:
    python tower_bot.py                 # run the loop
    python tower_bot.py --once          # single scan, useful while tuning
    python tower_bot.py --debug-scores  # log match scores for every template
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
import vision
from device import EmulatorError, Image, capture_screen, connect_device, tap
from vision import TemplateCache, locate_template

logger = logging.getLogger("tower_bot")


# --------------------------------------------------------------------------
# Bot
# --------------------------------------------------------------------------
class TowerBot:
    def __init__(
        self,
        device: AdbDevice,
        templates: TemplateCache,
        click_cooldown: float = config.CLICK_COOLDOWN_SECONDS,
        debug_scores: bool = False,
    ) -> None:
        self.device = device
        self.templates = templates
        self.click_cooldown = click_cooldown
        self.debug_scores = debug_scores
        self._screen: Image | None = None
        self._last_click: dict[str, float] = {}
        self._running = True

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
        brightness_ratio: float = config.DEFAULT_BRIGHTNESS_RATIO,
    ) -> bool:
        """Find ``template_path`` on the current screen and tap it.

        Returns True if the template was matched above ``threshold``, looked
        bright enough to be actionable, and a tap was sent. Uses the frame
        captured by the most recent :meth:`refresh_screen` call so one capture
        can serve many lookups.
        """
        key = str(template_path)
        template = self.templates.get(template_path)

        match = locate_template(self.screen, template, threshold)
        if match is None:
            if self.debug_scores:
                score, _ = vision.best_score(self.screen, template)
                logger.debug("%s: no match (best score %.3f)", key, score)
            return False

        (x, y), score = match.center, match.score

        # The match score is brightness-invariant, so check the actual grey
        # level too: a dimmed button is one the game is not offering yet.
        if brightness_ratio > 0.0:
            ratio = vision.brightness_ratio(self.screen, match, template)
            if self.debug_scores:
                logger.debug("%s: score %.3f, brightness %.2f of template", key, score, ratio)
            if ratio < brightness_ratio:
                logger.debug(
                    "%s: match at (%d, %d) skipped - dimmed (%.2f < %.2f)",
                    key, x, y, ratio, brightness_ratio,
                )
                return False

        now = time.monotonic()
        if now - self._last_click.get(key, 0.0) < self.click_cooldown:
            logger.debug("%s: match at (%d, %d) skipped - cooling down", key, x, y)
            return False

        tap(self.device, x, y)
        self._last_click[key] = now
        logger.info("Clicked %s at (%d, %d) [score %.3f]", key, x, y, score)
        return True

    # -- main loop ---------------------------------------------------------
    def run_once(self) -> bool:
        """One scan pass over the configured actions. True if anything clicked."""
        self.refresh_screen()
        clicked = False
        for action in config.ACTIONS:
            if self.find_and_click_image(
                action.template, action.threshold, action.brightness_ratio
            ):
                logger.info("Action performed: %s", action.name)
                clicked = True
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
        help="log the best match score for every template (use to tune thresholds)",
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

    bot = TowerBot(
        device=device,
        templates=TemplateCache(config.TEMPLATE_DIR),
        debug_scores=args.debug_scores,
    )

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s - shutting down after this scan.", signum)
        bot.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        bot.run_once()
    else:
        bot.run_forever(interval=args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
