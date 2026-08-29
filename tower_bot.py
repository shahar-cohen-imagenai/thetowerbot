"""Background automation bot for the Android game "The Tower".

Everything runs through ADB, so the emulator window never needs focus and the
mouse is never hijacked:

    screen capture  ->  device.screenshot()     (PIL image, converted in memory)
    template match  ->  cv2.matchTemplate
    click           ->  device.click(x, y)       (an `input tap` under the hood)

Usage:
    python tower_bot.py                 # run the loop
    python tower_bot.py --once          # a few scans, enough to settle on the real screen
    python tower_bot.py --debug-scores  # one frame, one table of every template's score
    python tower_bot.py --tui --auto-navigate  # live panel, loops runs unattended
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
import traceback
from types import FrameType

from adbutils import AdbDevice

import config
import events
import screens
import vision
from affordability import AffordabilityCheck, BrightnessAffordability
from device import EmulatorError, Image, capture_screen, connect_device, tap
from navigate import Navigator
from runs import RunTracker
from snapshots import SnapshotWriter
from sinks.log import LogSink
from sinks.tui import TuiSink

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
        auto_navigate: bool = False,
    ) -> None:
        self.device = device
        self.templates = templates
        self.bus = bus
        self.click_cooldown = click_cooldown
        self.affordability: AffordabilityCheck = affordability_check or BrightnessAffordability()
        self.tracker = screens.ScreenTracker()
        self.snapshots = SnapshotWriter(
            config.UNKNOWN_DIR, config.UNKNOWN_MIN_INTERVAL, config.UNKNOWN_KEEP
        )
        self.auto_navigate = auto_navigate
        self.navigator = Navigator(templates, bus)
        self.runs = RunTracker()
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
    def find_and_click_image(self, action: config.Action) -> bool:
        """Find the action's template on the current screen and tap it.

        Rejects are ordered cheapest-first (spec section 7): screen, then
        match score, then brightness, then cooldown.
        """
        key = action.template

        if self.tracker.state is not screens.ScreenState.IN_RUN:
            # Defence in depth, and deliberately silent. run_once already
            # skips the whole action loop and publishes exactly one
            # screen_gated event per scan; publishing here too would emit one
            # per action - 4 identical events every 2s, ~172k a day.
            return False

        template = self.templates.get(action.template)
        match = vision.locate_template(self.screen, template, action.threshold)
        if match is None:
            return False

        ok, detail = self.affordability.affordable(
            self.screen, match, template, action
        )
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
    def run_cap_reached(self, max_runs: int | None) -> bool:
        return max_runs is not None and self.runs.completed >= max_runs

    def run_once(self, max_runs: int | None = None) -> bool:
        """One scan pass over the configured actions. True if anything clicked.

        `max_runs` only suppresses auto-navigation. The scan that ends run N
        is also the scan that would tap RETRY on the way out, so without this
        the loop breaks at the top of the next iteration having already
        started run N+1 in the emulator.
        """
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
            run_event = self.runs.transition(self.tracker.state, time.monotonic())
            if run_event is not None:
                self.bus.publish(run_event)

        state = self.tracker.state

        # A CONFIRMED unknown, not the tracker's initial placeholder value.
        # Snapshotting on the placeholder means scan 1 of every launch saves
        # a perfectly recognisable screen; at 50 kept files, 50 launches
        # would evict every genuine one.
        if self.tracker.confirmed and state is screens.ScreenState.UNKNOWN:
            path = self.snapshots.maybe_write(self.screen)
            if path is not None:
                best = max(reading.scores, key=lambda name: reading.scores[name])
                self.bus.publish(
                    events.UnknownScreen(
                        snapshot_path=str(path),
                        best_anchor=best,
                        best_score=reading.scores[best],
                    )
                )

        # The screen gate is hoisted out of the action loop so an idle bot
        # emits ONE skip per scan rather than one per action.
        clicked = False
        if state is screens.ScreenState.IN_RUN:
            for action in config.ACTIONS:
                if self.find_and_click_image(action):
                    clicked = True
        else:
            self.bus.publish(
                events.Skipped(
                    action="*",
                    reason="screen_gated",
                    detail=f"screen is {state.value}",
                )
            )

        if self.auto_navigate and not self.run_cap_reached(max_runs):
            self.navigator.maybe_navigate(
                self.screen, state, self.device, now=time.monotonic()
            )

        self.bus.publish(
            events.ScanCompleted(
                screen=state.value,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        )
        return clicked

    def run_forever(
        self,
        interval: float = config.SCAN_INTERVAL_SECONDS,
        max_runs: int | None = None,
    ) -> None:
        logger.info("Bot started - scanning every %.1fs. Ctrl+C to stop.", interval)
        while self._running:
            # Checked before run_once(): if the limit is already reached at
            # entry, the loop must return without scanning at all, not after
            # one more pass.
            if self.run_cap_reached(max_runs):
                logger.info("Reached --max-runs=%d, stopping.", max_runs)
                break
            try:
                self.run_once(max_runs=max_runs)
            except EmulatorError as exc:
                logger.error("Device error: %s - retrying in %.1fs", exc, interval)
                self._report(f"Device error: {exc}")
            except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the loop
                logger.exception("Unexpected error during scan")
                self._report(f"Unexpected error during scan: {exc}")
            time.sleep(interval)
        logger.info("Bot stopped.")

    def _report(self, message: str) -> None:
        """Put a failure on the event stream, not just in the log.

        Under --tui the log is suppressed entirely, so this is the only way a
        device failure reaches the panel instead of silently stalling it.
        """
        self.bus.publish(
            events.BotError(message=message, traceback=traceback.format_exc())
        )

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
    parser.add_argument(
        "--once", action="store_true",
        help="scan just enough times for the screen tracker to settle, then exit",
    )
    parser.add_argument(
        "--debug-scores", action="store_true",
        help=(
            "one-shot diagnostic: capture a single frame and print every "
            "action's and screen anchor's match score (plus brightness "
            "ratio for actions), then exit without scanning or tapping"
        ),
    )
    parser.add_argument(
        "--tui", action="store_true", help="live terminal panel instead of log lines"
    )
    parser.add_argument(
        "--auto-navigate", action="store_true",
        help="tap RETRY / BATTLE to loop runs unattended (default: off)",
    )
    parser.add_argument(
        "--max-runs", type=int, default=None,
        help="stop after this many runs (default: unlimited)",
    )
    return parser.parse_args(argv)


def print_debug_scores(screen: Image, templates: vision.TemplateCache) -> None:
    """One-shot threshold-tuning diagnostic.

    Prints the best match score (and, for actions, the brightness ratio the
    affordability gate relies on) for every configured template against a
    single captured frame. Anchors are included deliberately: they are what
    you need to diagnose why a frame is reading as UNKNOWN.

    Self-contained by design - no debug flag threads through TowerBot itself.
    """
    print(f"{'action':<28}{'best_score':>12}{'brightness':>12}")
    for action in config.ACTIONS:
        template = templates.get(action.template)
        score, top_left = vision.best_score(screen, template)
        match = vision.Match(center=(0, 0), score=score, top_left=top_left)
        brightness = vision.brightness_ratio(screen, match, template)
        print(f"{action.template:<28}{score:>12.3f}{brightness:>12.3f}")

    print()
    print(f"{'screen anchor':<28}{'best_score':>12}")
    for name, template_path in config.SCREEN_ANCHORS.items():
        score, _ = vision.best_score(screen, templates.get(template_path))
        print(f"{name:<28}{score:>12.3f}")


def configure_logging(tui: bool) -> None:
    """Set up stdlib logging, or get it out of the TUI's way.

    rich's Live owns the terminal under --tui; stdlib logging writing to
    stderr draws straight over the panel. Nothing is lost by silencing it:
    failures reach the panel as BotError events on the bus.
    """
    if tui:
        logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()])
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.tui)

    try:
        device = connect_device(host=args.host, port=args.port)
    except EmulatorError as exc:
        logger.error("%s", exc)
        return 1

    if args.debug_scores:
        frame = capture_screen(device)
        print_debug_scores(frame, vision.TemplateCache(config.TEMPLATE_DIR))
        return 0

    bus = events.EventBus()
    sink = TuiSink() if args.tui else LogSink()
    bus.subscribe(sink)

    # Everything from start() onwards is inside the try: anything raising
    # between starting the consumer thread and the loop - a bad guard frame,
    # a template that will not load, signal registration off the main thread
    # - would otherwise leave that thread running and, under --tui, leave
    # rich's Live holding the terminal.
    try:
        sink.start()

        try:
            frame = capture_screen(device)
        except Exception as exc:  # noqa: BLE001 - a bad guard frame must not abort startup
            logger.warning("Could not capture a frame to verify resolution: %s", exc)
        else:
            height, width = frame.shape[:2]
            if (width, height) != config.EXPECTED_RESOLUTION:
                logger.warning(
                    "Emulator is %dx%d but templates were captured at %dx%d. "
                    "Template matching is not scale-invariant - re-capture them.",
                    width, height, *config.EXPECTED_RESOLUTION,
                )

        bot = TowerBot(
            device=device,
            templates=vision.TemplateCache(config.TEMPLATE_DIR),
            bus=bus,
            auto_navigate=args.auto_navigate,
        )

        def _handle_signal(signum: int, _frame: FrameType | None) -> None:
            logger.info("Received signal %s - shutting down after this scan.", signum)
            bot.stop()

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        if args.once:
            # A single scan never settles the debounced tracker (it needs
            # SCREEN_CONFIRMATIONS consecutive identical readings), so a
            # lone run_once() would always report UNKNOWN even when the
            # game is clearly on GAME_OVER at 0.998. Scan enough times to
            # settle so --once actually names the real screen.
            for _ in range(config.SCREEN_CONFIRMATIONS):
                bot.run_once()
        else:
            bot.run_forever(interval=args.interval, max_runs=args.max_runs)
    finally:
        sink.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
