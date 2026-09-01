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
import dataclasses
import ipaddress
import logging
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any

from adbutils import AdbDevice

if TYPE_CHECKING:
    from fastapi import FastAPI

import config
import db
import digits
import events
import screens
import vision
from affordability import (
    AffordabilityCheck,
    BrightnessAffordability,
    DigitAffordability,
)
from control import Controls
from device import EmulatorError, Image, capture_screen, connect_device, tap
from frames import FrameBuffer
from navigate import Navigator
from runs import RunTracker
from snapshots import SnapshotWriter
from sinks.log import LogSink
from sinks.sse import SseSink
from sinks.state import BotState, StateSink
from sinks.store import StoreSink
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
        controls: Controls | None = None,
        checks: dict[str, AffordabilityCheck | None] | None = None,
        reader: digits.NumberReader | None = None,
        first_run_id: int = 1,
        frames: FrameBuffer | None = None,
    ) -> None:
        self.device = device
        self.templates = templates
        self.bus = bus
        # Optional: --tui and --once have nobody to show a frame to, and every
        # test predating this constructs a bot without one.
        self.frames = frames
        self.click_cooldown = click_cooldown
        self.affordability: AffordabilityCheck = affordability_check or BrightnessAffordability()
        self.reader = reader if reader is not None else digits.NumberReader()
        self.wallet: int | None = None
        self.tracker = screens.ScreenTracker()
        self.snapshots = SnapshotWriter(
            config.UNKNOWN_DIR, config.UNKNOWN_MIN_INTERVAL, config.UNKNOWN_KEEP
        )
        # One source of truth for every live setting. A separate auto_navigate
        # attribute alongside this would be two, and they would drift.
        self.controls = controls if controls is not None else Controls()
        # Built once, here, and never in a request handler. A None entry means
        # that strategy is unavailable on this machine - which is what lets
        # PATCH /api/control refuse it with a reason instead of silently
        # handing back a different check.
        self.checks: dict[str, AffordabilityCheck | None] = checks or {
            self.controls.strategy: self.affordability
        }
        self.navigator = Navigator(templates, bus)
        self.runs = RunTracker(first_run_id)
        self._screen: Image | None = None
        self._last_click: dict[str, float] = {}
        self._running = True
        # Makes the between-scan sleep interruptible. A plain time.sleep()
        # ignores stop(): PEP 475 means it resumes after a signal handler
        # returns rather than aborting, and at a browser-set MAX_INTERVAL of
        # 3600s that turns Ctrl+C into an hour-long hang.
        self._stopping = threading.Event()

    @property
    def screen_state(self) -> screens.ScreenState:
        return self.tracker.state

    # -- screen state ------------------------------------------------------
    def refresh_screen(self) -> Image:
        """Capture a fresh frame and keep it as the current screen."""
        self._screen = capture_screen(self.device)
        if self.frames is not None:
            self.frames.publish(self._screen)
        return self._screen

    @property
    def screen(self) -> Image:
        if self._screen is None:
            return self.refresh_screen()
        return self._screen

    # -- the core helper ---------------------------------------------------
    def find_and_click_image(
        self, action: config.Action, boxes: list[dict[str, Any]] | None = None
    ) -> bool:
        """Find the action's template on the current screen and tap it.

        Rejects are ordered cheapest-first (spec section 7): screen, then
        match score, then brightness, then cooldown.

        `boxes`, when given, collects this match for the overlay - a plain
        local list owned by run_once() for the whole scan, not FrameBuffer
        directly. run_once() hands the finished list to frames.set_boxes()
        in one atomic swap once every action has been tried, rather than
        each match landing in the buffer the instant it is found: a reader
        between two such landings would catch the frame's matches only
        partially drawn.
        """
        # `cooldown_key` is purely internal - a stable per-template handle
        # for `_last_click`. `name` is what leaves the class: it is what
        # every Tapped/Skipped event carries, so the log, the dashboard feed
        # and the stored `events.action` column all speak the same
        # vocabulary as the control page, which gates on action.name too.
        cooldown_key = action.template
        name = action.name

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

        # The BUY point, not the label's own centre - config.buy_point()
        # documents why: the label is itself a button, so a tap there (or a
        # crosshair drawn there) marks the wrong square. Computed once, up
        # front, so the box recorded below and the eventual tap agree.
        tap_x, tap_y = config.buy_point(match.top_left)

        box: dict[str, Any] | None = None
        if boxes is not None:
            # Recorded whether or not the tap happens, because "matched but
            # rejected" is exactly what you open the device view to see.
            height, width = template.shape[:2]
            box = {
                "name": name,
                "x": int(match.top_left[0]),
                "y": int(match.top_left[1]),
                "w": int(width),
                "h": int(height),
                "tap_x": int(tap_x),
                "tap_y": int(tap_y),
                "score": float(match.score),
                "tapped": False,
            }
            boxes.append(box)

        ok, detail = self.affordability.affordable(
            self.screen, match, template, action
        )
        if not ok:
            # "unaffordable" means we read both numbers and the wallet was
            # short. "dimmed" means we could not tell and fell back to the
            # brightness heuristic. Collapsing them would hide exactly the
            # regression phase 3 exists to fix.
            reason = (
                "unaffordable"
                if self.affordability.last_price is not None
                else "dimmed"
            )
            self.bus.publish(
                events.Skipped(action=name, reason=reason, detail=detail)
            )
            return False

        now = time.monotonic()
        if now - self._last_click.get(cooldown_key, 0.0) < self.click_cooldown:
            self.bus.publish(events.Skipped(action=name, reason="cooldown"))
            return False

        tap(self.device, tap_x, tap_y)
        self._last_click[cooldown_key] = now
        self.bus.publish(
            events.Tapped(
                action=name,
                x=tap_x,
                y=tap_y,
                score=match.score,
                price=self.affordability.last_price,
                wallet=self.affordability.last_wallet,
            )
        )
        if box is not None:
            # `box` is still the same dict already appended to `boxes` above
            # - the swap into FrameBuffer has not happened yet, so there is
            # nothing there yet for mark_tapped() to find by name. Flipping
            # the local dict in place is what mark_tapped() would do to it
            # once it is FrameBuffer's; FrameBuffer.mark_tapped() itself
            # stays available for a caller that already holds published
            # boxes (see its own tests).
            box["tapped"] = True
        return True

    # -- the death modal ---------------------------------------------------
    def _read_modal_stats(
        self, ended: events.RunEnded, anchor: tuple[int, int]
    ) -> events.RunEnded:
        """Fill wave / coins / tier from the death modal.

        Only ever called on a CONFIRMED game over, so the modal has already
        survived two consecutive readings and its fade has finished - the same
        debounce that stops phantom run boundaries also guarantees the numbers
        are fully drawn.

        Wave holds a constant offset from the matched game_over template, but
        tier and coins do not: a record run grows a "New Highest Wave!" line
        that pushes them down 49px while the modal's top edge rises as it
        re-centres. Those two are found by their own caption instead.
        """
        return dataclasses.replace(
            ended,
            wave=self.reader.read(
                self.screen, config.MODAL_WAVE_REGION, anchor, "modal"
            ),
            coins=self.reader.read_at_caption(
                self.screen, config.MODAL_COINS_CAPTION, config.MODAL_COINS_REGION,
                "modal",
            ),
            tier=self.reader.read_at_caption(
                self.screen, config.MODAL_TIER_CAPTION, config.MODAL_TIER_REGION,
                "modal",
            ),
        )

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
        # Exactly one snapshot for the whole pass. Re-reading mid-scan would
        # let a setting change underneath a half-finished scan - the wallet
        # read with one strategy and the price gate applied with another.
        settings = self.controls.snapshot()
        chosen = self.checks.get(settings["strategy"])
        if chosen is not None:
            self.affordability = chosen
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
                if (
                    isinstance(run_event, events.RunEnded)
                    and self.tracker.state is screens.ScreenState.GAME_OVER
                    and reading.top_left is not None
                ):
                    run_event = self._read_modal_stats(run_event, reading.top_left)
                self.bus.publish(run_event)

        state = self.tracker.state

        # The wallet region is anchored to the IN_RUN template, so it can only
        # be read on that screen. Clear it elsewhere: a stale wallet would let
        # the affordability gate approve a purchase using last run's cash.
        #
        # BOTH states, not just the tracker's: the tracker is debounced, so
        # mid-fade it still says IN_RUN while the frame is already the death
        # modal. `reading.top_left` is then the GAME_OVER anchor, and the
        # wallet region measured from it lands somewhere else entirely. The
        # anchor and the region have to come from the same frame.
        self.wallet = None
        if (
            state is screens.ScreenState.IN_RUN
            and reading.state is screens.ScreenState.IN_RUN
            and reading.top_left is not None
        ):
            self.wallet = self.reader.read(
                self.screen, config.WALLET_REGION, reading.top_left, "wallet"
            )
        if isinstance(self.affordability, DigitAffordability):
            self.affordability.wallet = self.wallet

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
        # Collected locally and swapped into `frames` in one atomic call
        # once the loop below is done, rather than each match landing there
        # the instant it is found - see FrameBuffer.set_boxes(). Stays empty
        # here whenever the loop below does not run (paused, screen-gated),
        # matching add_box() never having been called in those cases before.
        boxes: list[dict[str, Any]] = []
        if settings["paused"]:
            # Still scanning, still reporting - just not acting. One skip per
            # scan, not one per action, matching the screen gate below.
            self.bus.publish(
                events.Skipped(action="*", reason="paused", detail="paused from the dashboard")
            )
        elif state is screens.ScreenState.IN_RUN:
            enabled = set(settings["enabled_actions"])
            for action in config.ACTIONS:
                if action.name not in enabled:
                    continue
                if self.find_and_click_image(action, boxes):
                    clicked = True
        else:
            self.bus.publish(
                events.Skipped(
                    action="*",
                    reason="screen_gated",
                    detail=f"screen is {state.value}",
                )
            )

        if self.frames is not None:
            self.frames.set_boxes(boxes)

        if (
            settings["auto_navigate"]
            and not settings["paused"]
            and not self.run_cap_reached(max_runs)
        ):
            self.navigator.maybe_navigate(
                self.screen, state, self.device, now=time.monotonic()
            )

        self.bus.publish(
            events.ScanCompleted(
                screen=state.value,
                duration_ms=(time.monotonic() - started) * 1000,
                wallet=self.wallet,
            )
        )
        return clicked

    def run_forever(
        self,
        interval: float | None = None,
        max_runs: int | None = None,
    ) -> None:
        """Run scans back to back until stop() is called or max_runs is hit.

        `interval` has two distinct meanings, deliberately:

        - `None` (the default, and what serve_web() passes in production)
          means the dashboard owns the pace. `self.controls.interval` is
          re-read at the top of every iteration, so a change made from the
          browser takes effect on the very next sleep rather than requiring a
          restart.
        - An explicit number is a caller override. It is used exactly as
          given, every iteration, and deliberately never written into
          Controls - this is the seam the tests use to run the loop without
          sleeping (`run_forever(interval=0.0)`). Controls.apply() enforces
          MIN_INTERVAL/MAX_INTERVAL because a browser-supplied value has to
          be sane; a test's 0.0 is not a browser and must not be bound by
          those rules.

        The between-scan wait is `self._stopping.wait(...)`, not
        `time.sleep(...)`: stop() sets that event, so a shutdown ends the
        wait immediately regardless of how large the interval is, rather
        than sleeping it out (which a plain time.sleep() would do - PEP 475
        resumes it after a signal handler returns instead of aborting it).
        """
        startup_interval = self.controls.interval if interval is None else interval
        logger.info(
            "Bot started - scanning every %.1fs. Ctrl+C to stop.", startup_interval
        )
        while self._running:
            # Checked before run_once(): if the limit is already reached at
            # entry, the loop must return without scanning at all, not after
            # one more pass.
            if self.run_cap_reached(max_runs):
                logger.info("Reached --max-runs=%d, stopping.", max_runs)
                break
            # Re-read every iteration (when interval is None) rather than
            # once at the top of the loop - see the docstring above.
            current_interval = self.controls.interval if interval is None else interval
            try:
                self.run_once(max_runs=max_runs)
            except EmulatorError as exc:
                logger.error("Device error: %s - retrying in %.1fs", exc, current_interval)
                self._report(f"Device error: {exc}")
            except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the loop
                logger.exception("Unexpected error during scan")
                self._report(f"Unexpected error during scan: {exc}")
            self._stopping.wait(current_interval)
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
        # Interrupts an in-flight self._stopping.wait() in run_forever(), so
        # shutdown does not have to wait out whatever interval is current.
        self._stopping.set()


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
    parser.add_argument(
        "--affordability", choices=("digits", "brightness"), default="digits",
        help=(
            "how to decide an upgrade is buyable: read the numbers (default, "
            "exact) or compare brightness (the older heuristic). digits falls "
            "back to brightness on its own when no atlas is built"
        ),
    )
    parser.add_argument(
        "--web", action="store_true",
        help="serve the dashboard while the bot runs (default: off)",
    )
    parser.add_argument(
        "--web-host", default=config.WEB_HOST,
        help="dashboard bind address - loopback by default, and there is no auth",
    )
    parser.add_argument("--web-port", type=int, default=config.WEB_PORT)
    parser.add_argument(
        "--db", default=str(config.DB_PATH), help="SQLite file for the event log"
    )
    parser.add_argument(
        "--no-store", dest="store", action="store_false", default=True,
        help="do not persist events to SQLite",
    )
    return parser.parse_args(argv)


def build_affordability(
    strategy: str, atlas_root: Path | None = None
) -> AffordabilityCheck:
    """Pick the affordability check, degrading when digits are unavailable.

    Digits need a labelled atlas that only exists once someone has run
    build_atlas.py. Without one, fall back rather than fail: brightness is the
    floor, and a bot that refuses to start is worse than one that guesses at
    brightness like it did before.

    Every size class has to be present, not just one. The price is what gates
    a purchase, so a bot that could read the wallet but never the price would
    be gating on nothing at all.
    """
    if strategy == "brightness":
        return BrightnessAffordability()

    cache = digits.AtlasCache(
        atlas_root if atlas_root is not None else config.ATLAS_DIR
    )
    missing = [name for name in digits.SIZE_CLASSES if cache.get(name) is None]
    if missing:
        logger.warning(
            "no glyph atlas for %s - falling back to brightness affordability. "
            "Run: uv run build_atlas.py --size-class <name>",
            ", ".join(missing),
        )
        return BrightnessAffordability()

    return DigitAffordability(digits.NumberReader(cache), BrightnessAffordability())


def build_checks_and_controls(
    args: argparse.Namespace, atlas_root: Path | None = None
) -> tuple[dict[str, AffordabilityCheck | None], Controls]:
    """Build every affordability strategy once, and seed Controls from what
    actually got built rather than from `args.affordability` alone.

    Both strategies built once, at startup. `digits` degrades to brightness
    when no atlas exists, and the identity check below is how we notice - so
    the dashboard can refuse a switch to digits with a reason rather than
    quietly handing back brightness.

    `atlas_root` exists so this can be exercised without a device: it is
    threaded straight through to `build_affordability`.
    """
    brightness = BrightnessAffordability()
    digits_check = build_affordability("digits", atlas_root=atlas_root)
    checks: dict[str, AffordabilityCheck | None] = {
        "brightness": brightness,
        "digits": digits_check if isinstance(digits_check, DigitAffordability) else None,
    }
    controls = Controls(
        interval=args.interval,
        auto_navigate=args.auto_navigate,
        strategy=args.affordability if checks.get(args.affordability) else "brightness",
    )
    return checks, controls


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


def warn_if_web_host_exposed(host: str) -> None:
    """Nudge for the "reach it from my laptop" reflex.

    config.WEB_HOST's docstring carries the real warning, but nobody reads a
    default's docstring on the way to overriding it with --web-host. The
    dashboard serves live screenshots and full event history with no auth,
    and - now that the control plane is wired in - lets a caller pause,
    reconfigure or stop the bot too, so binding anything but loopback
    deserves pushback at the point someone is actually about to do it.
    """
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not a literal IP (a hostname, a typo) - can't prove it is loopback,
        # so treat it the same as "not loopback" rather than staying silent.
        loopback = False
    if not loopback:
        logger.warning(
            "--web-host %s is not loopback - the dashboard's live "
            "screenshots, event history, and control over the bot (pause, "
            "reconfigure, stop) will be reachable by anyone on this "
            "network, and there is no authentication.",
            host,
        )


def install_signal_handlers(bot: TowerBot) -> None:
    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s - shutting down after this scan.", signum)
        bot.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def prepare_store(
    path: Path, retention_days: int = config.EVENT_RETENTION_DAYS
) -> tuple[int, int]:
    """Create the database, prune it, and report what to seed the counters to.

    Returns `(max_seq, max_run_id)`. Both are in-process counters that would
    otherwise restart at zero on every launch: seq would collide with stored
    rows on the events primary key and break SSE resume across a restart, and
    run ids would overwrite the previous session's runs one at a time.

    Read BEFORE pruning, deliberately: a seq that was already handed out must
    never be reissued, even for an event old enough to have just aged out of
    retention. Pruning is disk hygiene, not a reason to rewind the counter.

    Also closes out any run a killed process left with `ended_at IS NULL`:
    without a RunEnded, the row - and the dashboard's "live" badge on it -
    would otherwise persist forever. This is the only place that legitimately
    holds the writable connection outside the store sink's own thread, so it
    is the one place that can fix that up.
    """
    conn = db.connect(path)
    try:
        seed_seq = db.max_seq(conn)
        last_run = db.max_run_id(conn)
        abandoned = db.close_abandoned_runs(conn)
        if abandoned:
            logger.info("Closed %d run(s) left live by a killed process", abandoned)
        removed = db.prune_events(conn, retention_days)
        if removed:
            logger.info("Pruned %d events older than %d days", removed, retention_days)
        return seed_seq, last_run
    finally:
        conn.close()


def serve_web(
    bot: TowerBot,
    app: FastAPI,
    *,
    host: str,
    port: int,
    max_runs: int | None,
    stop: threading.Event | None = None,
) -> None:
    """Run the server on this thread and the scan loop beside it.

    No `interval` parameter: the dashboard owns the pace once --web is on, so
    the scan loop is started with `run_forever(interval=None)` and reads
    `bot.controls.interval` for itself on every iteration. Passing an
    interval here would be a second source of truth for the same value.

    This way round on purpose. uvicorn installs its own SIGINT/SIGTERM
    handlers and can only do that from the main thread, so it gets the main
    thread and the scan loop gets a worker. They genuinely run in parallel
    despite the GIL: cv2.matchTemplate releases it for the duration of the
    match, which is where a scan spends nearly all of its time.

    uvicorn.run() hides its Server object, so there is nothing to tell it to
    stop once --max-runs is reached and the worker simply ends - the server
    would then serve a stopped bot forever. Owning the Server instead gives
    the worker a way to bring the server down too, whichever way the loop
    exits.

    `stop` also has to be watched, not just set: the stop route (see
    web/app.py's stop_bot()) has no handle on the worker thread or the
    Server - the shared Event is the only thing it can reach from a request
    handler. Without something waiting on it, setting the flag there did
    nothing but kill SSE streams (see event_stream() below), while the scan
    loop and the server both ran on regardless - the dashboard looked stopped
    and was not. The watcher thread below is that missing waiter, and it is
    what keeps this the single shutdown path the docstring above describes:
    every way to stop - Ctrl+C, --max-runs, and now the browser - ends up
    setting the same `stop` and `should_exit`, rather than the browser route
    needing its own bespoke teardown.

    `stop` is the fix for a second, worse hang: with an SSE tab open, the
    in-flight `/api/events/stream` response never disconnects on its own, so
    uvicorn's graceful shutdown - which only closes a connection once its
    response finishes - waits on it forever while the scan loop, having only
    `server.should_exit`, keeps right on playing. Setting `stop` lets the SSE
    generator (see event_stream()) end itself so the response completes and
    the connection closes normally. It is set in both places the scan loop
    can end (the worker's `finally`, same as `should_exit`) and in this
    function's own `finally`, so a stream started after the worker already
    exited is still caught. `timeout_graceful_shutdown` below is the backstop
    for everything `stop` does not cover - a route this bot never had that
    ignores the flag, say - two seconds being long enough to drain a real
    response and short enough that a wedge is still a blip, not a hang.
    """
    import uvicorn

    if stop is None:
        stop = threading.Event()

    class _Server(uvicorn.Server):
        """Set `stop` the instant uvicorn decides to exit, not after.

        uvicorn installs its own SIGINT/SIGTERM handler and, on the main
        thread, runs graceful shutdown to completion inside it before
        server.run() ever returns. `stop` was previously only set in this
        function's own `finally` below - which does not run until
        server.run() returns - so with a stream held open (an SSE tab, or
        now the device view), the generators never saw the flag during
        graceful shutdown at all: it waited out the full
        timeout_graceful_shutdown backstop, force-cancelling the response,
        every single time. Overriding handle_exit() sets `stop` before
        calling through to uvicorn's own handling, so event_stream() and
        frame_stream() can end themselves - and the response complete -
        while graceful shutdown is still in its normal (short) path, rather
        than needing the backstop to end it.
        """

        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            stop.set()
            super().handle_exit(sig, frame)

    config_ = uvicorn.Config(
        app, host=host, port=port, log_level="warning",
        # Backstop, not the fix: without `stop` this defaults to None, which
        # is "wait forever" (uvicorn/config.py) - the exact hang finding 1
        # describes. With `stop` (and _Server.handle_exit setting it before
        # graceful shutdown begins) this should never fire in practice.
        timeout_graceful_shutdown=2,
    )
    server = _Server(config_)

    def _run_loop() -> None:
        try:
            bot.run_forever(max_runs=max_runs)
        finally:
            # Whether the loop returned because it hit --max-runs or because
            # it raised, the server has nothing left to serve for - and any
            # open SSE stream has to be told too, or it never notices.
            server.should_exit = True
            stop.set()

    def _watch_stop() -> None:
        # The only waiter on `stop`. See the docstring above: the stop route
        # can set the flag but has no other way to reach the loop or the
        # server, so this thread is what actually turns "stop was requested"
        # into "the bot stopped". Harmless on every other exit path (Ctrl+C,
        # --max-runs) - `stop` is already set there by the time this wakes
        # up, so it just repeats a no-op stop() and should_exit assignment.
        stop.wait()
        bot.stop()
        server.should_exit = True

    threading.Thread(target=_watch_stop, name="stop-watch", daemon=True).start()

    worker = threading.Thread(target=_run_loop, name="scan-loop", daemon=True)
    worker.start()
    try:
        server.run()
    finally:
        # server.run() returns on Ctrl+C (uvicorn's own handler) or when the
        # loop above set should_exit; the loop must be told either way, or
        # the process hangs around scanning with nothing watching. Setting
        # `stop` here too covers Ctrl+C, which the worker's own finally never
        # sees since the scan loop itself did not end.
        bot.stop()
        stop.set()
        # bot.stop() interrupts the between-scan wait immediately regardless
        # of the current interval (see run_forever's docstring), so this
        # join is only a backstop for a scan already in flight - not a
        # deadline sized to the interval itself.
        #
        # Bounded on purpose: the browser can set the interval as high as
        # MAX_INTERVAL, and a shutdown must not inherit that as its deadline.
        worker.join(timeout=min(bot.controls.interval, 5.0) + 2.0)


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

    db_path = Path(args.db)
    seed_seq, last_run = prepare_store(db_path) if args.store else (0, 0)

    bus = events.EventBus(start_seq=seed_seq)
    state = BotState()
    sinks: list[events.Sink] = [TuiSink(state=state) if args.tui else LogSink()]
    if args.store:
        sinks.append(StoreSink(db_path))
    if args.web and not args.tui:
        # Under --tui the panel's sink already feeds the shared state.
        sinks.append(StateSink(state))
    sse = SseSink() if args.web else None
    frames = FrameBuffer() if args.web else None
    # Set once the scan loop and/or the server ends, whichever comes first -
    # see serve_web() and event_stream() for why a held-open dashboard tab
    # needs telling separately from uvicorn's own should_exit.
    stop = threading.Event()

    for sink in sinks:
        bus.subscribe(sink)
    if sse is not None:
        bus.subscribe(sse)  # no thread to start: it appends and returns

    if args.web and not args.once:
        # print(), not logger.info(), and before sink.start() below: under
        # --tui, TuiSink.start() hands the terminal to rich's Live, and
        # configure_logging(tui=True) sets the root logger to CRITICAL with
        # a NullHandler either way - both would silently swallow this line
        # if it ran any later (task 8, minor 6). `not args.once` alongside
        # that: --once wins over --web, so printing unconditionally would
        # advertise a dashboard that never starts (M1).
        print(f"Dashboard on http://{args.web_host}:{args.web_port}")

    # Everything from start() onwards is inside the try: anything raising
    # between starting the consumer threads and the loop would otherwise
    # leave them running and, under --tui, leave rich's Live holding the
    # terminal.
    try:
        for sink in sinks:
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

        checks, controls = build_checks_and_controls(args)
        bot = TowerBot(
            device=device,
            templates=vision.TemplateCache(config.TEMPLATE_DIR),
            bus=bus,
            # checks[controls.strategy] is never None here: build_checks_and_controls()
            # already seeded controls.strategy to a name whose check built
            # (falling back to "brightness" itself when it did not), so a
            # "checks[...] or checks['brightness']" fallback would be dead code.
            affordability_check=checks[controls.strategy],
            controls=controls,
            checks=checks,
            first_run_id=last_run + 1,
            frames=frames,
        )

        if args.once:
            # A single scan never settles the debounced tracker (it needs
            # SCREEN_CONFIRMATIONS consecutive identical readings), so a
            # lone run_once() would always report UNKNOWN even when the
            # game is clearly on GAME_OVER at 0.998. Scan enough times to
            # settle so --once actually names the real screen.
            install_signal_handlers(bot)
            for _ in range(config.SCREEN_CONFIRMATIONS):
                bot.run_once()
        elif args.web:
            warn_if_web_host_exposed(args.web_host)

            from web.app import create_app

            app = create_app(
                state=state, sse=sse, bus=bus,
                db_path=db_path if args.store else None,
                stop=stop,
                controls=controls,
                checks=checks,
                frames=frames,
            )
            # No signal handlers of ours here: uvicorn installs its own and
            # would overwrite them anyway.
            serve_web(
                bot, app, host=args.web_host, port=args.web_port,
                max_runs=args.max_runs,
                stop=stop,
            )
        else:
            install_signal_handlers(bot)
            # No explicit interval: bot.controls was already seeded from
            # args.interval above, and that stays the one source of truth for
            # it even without --web.
            bot.run_forever(max_runs=args.max_runs)
    finally:
        for sink in sinks:
            sink.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
