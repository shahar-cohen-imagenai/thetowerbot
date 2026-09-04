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
    python tower_bot.py --tui           # live panel instead of log lines
    python tower_bot.py --web           # dashboard: pause, retune, loop runs
    python tower_bot.py --web --idle    # dashboard with no bot - press Start
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
import jitter
import ocr
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
from runner import BotRunner, RunnerError
from runs import RunTracker
from shopping import ShoppingSession
from snapshots import SnapshotWriter
from sinks.log import LogSink
from sinks.sse import SseSink
from sinks.state import BotState, StateSink
from sinks.store import StoreSink
from sinks.tui import TuiSink
from strategy import MIN_INTERVAL, ControlError, Strategy, StrategyStore

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
        affordability_check: AffordabilityCheck | None = None,
        controls: Controls | None = None,
        checks: dict[str, AffordabilityCheck | None] | None = None,
        reader: digits.NumberReader | None = None,
        shopping: ShoppingSession | None = None,
        first_run_id: int = 1,
        frames: FrameBuffer | None = None,
        screen_confirmations: int = config.SCREEN_CONFIRMATIONS,
        navigation_cooldown: float = config.NAVIGATION_COOLDOWN_SECONDS,
    ) -> None:
        self.device = device
        self.templates = templates
        self.bus = bus
        # Optional: --tui and --once have nobody to show a frame to, and every
        # test predating this constructs a bot without one.
        self.frames = frames
        self.affordability: AffordabilityCheck = affordability_check or BrightnessAffordability()
        self.reader = reader if reader is not None else digits.NumberReader()
        # Built once, per process, by build_shopping() - see that function's
        # docstring for why (the header glyph atlas it gates on is exactly as
        # expensive to build as the digit atlas `checks` already amortises).
        # Absent entirely, every existing test that constructs a bot without
        # `shopping=` still gets a session - just one that carries its own
        # reason and declines every begin(), rather than an AttributeError
        # the first time run_once() reaches self.shopping.active.
        self.shopping: ShoppingSession = shopping if shopping is not None else ShoppingSession(
            templates, bus, self.reader,
            disabled_reason="no shopping session was configured for this bot",
        )
        self.wallet: int | None = None
        # Read once, here, rather than per scan: both configure an object
        # that carries state across scans (the tracker's part-confirmed
        # reading, the navigator's last-navigation timestamp), and changing
        # either under a running one has no correct answer. This is the
        # "applies on next Start" boundary the dashboard labels.
        self.tracker = screens.ScreenTracker(confirmations=screen_confirmations)
        self.snapshots = SnapshotWriter(
            config.UNKNOWN_DIR, config.UNKNOWN_MIN_INTERVAL, config.UNKNOWN_KEEP
        )
        # One source of truth for every live setting. A Controls built here
        # would need a Strategy to hold, and inventing one would compete with
        # StrategyStore.ensure_seeded() - so callers build it and pass it.
        self.controls = controls if controls is not None else Controls(
            strategy=Strategy.from_config()
        )
        # Built once, here, and never in a request handler. A None entry means
        # that strategy is unavailable on this machine - which is what lets
        # PATCH /api/control refuse it with a reason instead of silently
        # handing back a different check.
        self.checks: dict[str, AffordabilityCheck | None] = checks or {
            self.controls.snapshot().strategy.affordability: self.affordability
        }
        self.navigator = Navigator(templates, bus, cooldown=navigation_cooldown)
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
        self,
        action: config.Action,
        boxes: list[dict[str, Any]] | None = None,
        tuning: Strategy | None = None,
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

        `tuning`, when given, carries every policy value this method
        reads - the click_cooldown to gate against and the three jitter
        numbers - as ONE object, which run_once() takes from the single
        snapshot at the top of its pass. `None` (the default) falls back to
        a fresh `self.controls.snapshot()` here instead, which is what lets
        a test or any other direct caller invoke this method without first
        constructing a settings object of its own. One object rather than a
        scalar per field is deliberate: the four values must describe the
        same instant as each other, and threading them separately is how
        they would eventually stop doing so. Re-reading per call is exactly
        what the loop path must NOT do, though: this
        method is called once per matched rule inside run_once()'s action
        loop without a `break`, and a PATCH landing between two of those
        calls (run_forever and serve_web run on different threads) would
        otherwise let one row's cooldown be judged against a click_cooldown
        from a different instant than the strategy that selected and
        ordered the rows - the same class of hazard run_once()'s own
        docstring warns about for the wallet and the price gate, just on a
        narrower field.
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
        #
        # Jittered HERE, before the box below is recorded and before the
        # Tapped event is published, rather than inside device.tap(): the
        # overlay's crosshair and the event's coordinates must be the pixel
        # actually tapped, not the pixel we would have tapped without
        # jitter. Putting the offset in tap() would make both lie by up to
        # tap_jitter_px, on exactly the page you open to find out why a
        # purchase missed.
        policy = self.controls.snapshot().strategy if tuning is None else tuning
        tap_x, tap_y = jitter.point(
            *config.buy_point(match.top_left), policy.tap_jitter_px
        )

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
        # stretch(), not spread(): click_cooldown is a functional minimum
        # (see config.CLICK_COOLDOWN_SECONDS), so jitter may only ever make
        # the gap longer. Jittering it downward would re-admit the burst of
        # taps on an already-animating button that the cooldown exists to
        # stop.
        effective_cooldown = jitter.stretch(
            policy.click_cooldown, policy.timing_jitter
        )
        if now - self._last_click.get(cooldown_key, 0.0) < effective_cooldown:
            self.bus.publish(events.Skipped(action=name, reason="cooldown"))
            return False

        # The reaction gap. Passing the stop event's wait() as the sleeper
        # keeps Stop instant: a shutdown ends the pause instead of sleeping
        # it out.
        jitter.pause(
            policy.tap_delay, policy.timing_jitter, sleep=self._stopping.wait
        )
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
    def run_cap_reached(
        self, max_runs: int | None = None, strategy: Strategy | None = None
    ) -> bool:
        """Has the bot completed as many runs as it was asked for?

        The explicit parameter wins when set - that is the non-web path,
        which has a CLI flag and no strategy loaded. Otherwise the strategy
        supplies it. The two can never both be meaningfully set in the web
        path, because the CLI persists its flag into the strategy rather
        than carrying it alongside (see the spec, section 10).

        The `strategy` fallback to a fresh snapshot is NOT the re-read hazard
        that was removed from find_and_click_image: run_once always hands
        down the snapshot its own pass took, so a mid-pass PATCH cannot split
        one scan across two policies, while run_forever calls this with no
        strategy *between* passes - where reading the newest one is the whole
        point, because a run cap raised from the dashboard should take effect
        on the next iteration rather than the next restart.
        """
        cap = max_runs
        if cap is None:
            source = strategy if strategy is not None else self.controls.snapshot().strategy
            cap = source.max_runs
        return cap is not None and self.runs.completed >= cap

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
        chosen = self.checks.get(settings.strategy.affordability)
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

        # A visit owns the frame while it runs. Three things below key off
        # this rather than off the screen state, because the pages a visit
        # walks are UNKNOWN to the tracker by design - see pages.py.
        visiting = self.shopping.active

        # A CONFIRMED unknown, not the tracker's initial placeholder value.
        # Snapshotting on the placeholder means scan 1 of every launch saves
        # a perfectly recognisable screen; at 50 kept files, 50 launches
        # would evict every genuine one. `not visiting` on top of that: a
        # workshop or cards page reads UNKNOWN to this tracker by design (see
        # pages.py), so without this guard every shopping visit would fill
        # unknown/ with pictures of the very pages it is deliberately
        # visiting, evicting the genuine unmodelled screens the directory
        # exists to hold.
        if self.tracker.confirmed and state is screens.ScreenState.UNKNOWN and not visiting:
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
        if settings.paused:
            # Still scanning, still reporting - just not acting. One skip per
            # scan, not one per action, matching the screen gate below.
            self.bus.publish(
                events.Skipped(action="*", reason="paused", detail="paused from the dashboard")
            )
        elif state is screens.ScreenState.IN_RUN:
            # The strategy's rows, in the strategy's order - order IS
            # priority. Before, this walked config.ACTIONS and used the
            # settings only as an on/off filter, so neither reordering nor
            # a per-row threshold could reach the matcher.
            for rule in settings.strategy.actions:
                if not rule.enabled:
                    continue
                if self.find_and_click_image(
                    rule.as_action(), boxes, tuning=settings.strategy
                ):
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
            settings.strategy.auto_navigate
            and not settings.paused
            and not self.run_cap_reached(max_runs, settings.strategy)
            and not visiting
        ):
            # Navigator taps BATTLE on MAIN_MENU on a cooldown - left alone
            # it would start a run in the middle of a shopping errand.
            self.navigator.maybe_navigate(
                self.screen,
                state,
                self.device,
                now=time.monotonic(),
                tuning=settings.strategy,
            )

        # Checked after navigation, and begin() checked after advance() below:
        # a visit that just ended this same scan must not restart within it,
        # and must not race the tap navigation just skipped above.
        if visiting and settings.paused:
            # Freeze, don't unwind. advance() is the one tap path that spends
            # currency, so pause has to suppress it too, not just the start
            # of a visit - "still scanning, not tapping" has to hold
            # mid-errand. Ending the visit instead would want a return-to-
            # Battle tap of its own, which is exactly what pause forbids;
            # `visiting` stays True below (shopping.active is untouched), so
            # navigation and unknown-snapshot suppression both stay in
            # force too - the bot is still sitting on a menu page either
            # way. The paused Skipped event published above already makes
            # this visible on the feed. The visit simply resumes, from
            # wherever it left off, on the next unpaused scan.
            pass
        elif visiting:
            self.shopping.advance(
                self.screen,
                self.device,
                settings.strategy.shopping,
                tuning=settings.strategy,
            )
        elif (
            state is screens.ScreenState.MAIN_MENU
            and not settings.paused
            and not self.run_cap_reached(max_runs, settings.strategy)
        ):
            # A visit begins from MAIN_MENU only, and never while paused or
            # capped - "still scanning, not tapping" has to cover this one
            # tap path too, since it is the one that spends currency.
            self.shopping.begin(settings.strategy.shopping, self.runs.completed)

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

        - `None` (the default, and what `BotRunner._run()` passes in
          production - see runner.py) means the dashboard owns the pace.
          `self.controls.snapshot().strategy.interval`
          is re-read at the top of every iteration, so a change made from the
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
        startup_interval = (
            self.controls.snapshot().strategy.interval if interval is None else interval
        )
        logger.info(
            "Bot started - scanning every %.1fs. Ctrl+C to stop.", startup_interval
        )
        while self._running:
            # Checked before run_once(): if the limit is already reached at
            # entry, the loop must return without scanning at all, not after
            # one more pass.
            #
            # One snapshot feeds both the check and the message. The cap is
            # logged RESOLVED rather than as the parameter, because in the
            # web path the parameter is None: BotRunner._run() calls
            # run_forever() with no arguments and the cap comes from the
            # strategy. "%d" % None raises inside logging, so the operator
            # would get a "--- Logging error ---" traceback at exactly the
            # moment the line exists to explain - the dashboard parking with
            # a stopped bot.
            capped = self.controls.snapshot().strategy
            if self.run_cap_reached(max_runs, capped):
                logger.info(
                    "Reached the run cap of %d, stopping.",
                    max_runs if max_runs is not None else capped.max_runs,
                )
                break
            # Re-read every iteration (when interval is None) rather than
            # once at the top of the loop - see the docstring above.
            if interval is None:
                live = self.controls.snapshot().strategy
                # spread(), not stretch(): unlike the cooldowns, the interval
                # is a target rather than a floor, and a loop that only ever
                # waited longer than its nominal interval would still be a
                # metronome - just a slower one. Clamped at MIN_INTERVAL so a
                # dial already at the floor cannot jitter below it into the
                # busy loop that floor exists to prevent.
                current_interval = max(
                    MIN_INTERVAL, jitter.spread(live.interval, live.timing_jitter)
                )
            else:
                # An explicit override is used exactly as given - see the
                # docstring. Tests pass 0.0 to run the loop without sleeping,
                # and jittering that would reintroduce the sleep.
                current_interval = interval
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
        "--interval", type=float, default=None,
        help=(
            "seconds between scans - overrides the active strategy AND is "
            "saved into it"
        ),
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
        # BooleanOptionalAction, so --no-auto-navigate exists too. With a
        # plain store_true the flag was a one-way switch: passing it saved
        # True into the strategy (see apply_cli_overrides), and nothing on
        # the command line could ever put it back - the only way off was the
        # dashboard. `default=None` still distinguishes "not passed" from
        # "passed False", which is what keeps an absent flag from
        # overwriting the saved profile.
        "--auto-navigate", action=argparse.BooleanOptionalAction, default=None,
        help="tap RETRY / BATTLE to loop runs unattended - overrides and saves",
    )
    parser.add_argument(
        "--max-runs", type=int, default=None,
        help="stop after this many runs - overrides and saves",
    )
    parser.add_argument(
        "--affordability", choices=("digits", "brightness"), default=None,
        help=(
            "how to decide an upgrade is buyable: read the numbers (exact) "
            "or compare brightness (the older heuristic) - overrides and "
            "saves. digits falls back to brightness on its own when no "
            "atlas is built"
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
    parser.add_argument(
        "--strategy", default=None,
        help="which saved strategy to load (default: the active one)",
    )
    parser.add_argument(
        "--idle", action="store_true",
        help=(
            "with --web, serve the dashboard without starting the bot - "
            "press Start in the browser"
        ),
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


def build_checks(
    atlas_root: Path | None = None,
) -> dict[str, AffordabilityCheck | None]:
    """Build every affordability strategy once. Safe to call on every start.

    `digits` degrades to brightness when no atlas exists, and a None entry
    here is how the rest of the app notices - see `_reconcile_affordability`
    for the one place that has to act on it.

    `atlas_root` exists so this can be exercised without a device: it is
    threaded straight through to `build_affordability`.

    Deliberately does not touch `Controls`. This returns a
    bot-affecting-only dict; `Controls` is built once, at process startup,
    and lives for as long as the server does - conflating the two here would
    make it possible to hand out a fresh `Controls` while the HTTP routes
    kept patching the old one, with the running bot never seeing the
    dashboard's edits.

    `BotRunner` does NOT call this: it is handed the dict in its constructor
    and reuses it for every bot it ever starts, because checks are
    per-process, not per-bot (rebuilding the glyph atlas on every Start would
    make the button slow for no gain). "Safe to call on every start" above is
    about this function being free of hidden state, not an invitation to.
    """
    brightness = BrightnessAffordability()
    digits_check = build_affordability("digits", atlas_root=atlas_root)
    return {
        "brightness": brightness,
        "digits": digits_check if isinstance(digits_check, DigitAffordability) else None,
    }


def _reconcile_affordability(
    loaded: Strategy, checks: dict[str, AffordabilityCheck | None]
) -> Strategy:
    """Downgrade `loaded.affordability` if the atlas this machine has cannot
    serve it.

    Must run exactly once, at the point the long-lived `Controls` is built -
    not on every start(), or a restart could silently re-seed a method the
    operator had already been downgraded away from. The CLI flags that
    override strategy fields are applied by the caller (see plan 3's
    --strategy handling), not here: this function's job is only to reconcile
    the requested policy with the checks that actually built.
    """
    affordability = loaded.affordability
    if checks.get(affordability) is None:
        affordability = "brightness"
    return dataclasses.replace(loaded, affordability=affordability)


def build_checks_and_controls(
    loaded: Strategy, atlas_root: Path | None = None
) -> tuple[dict[str, AffordabilityCheck | None], Controls]:
    """Thin wrapper over build_checks() + _reconcile_affordability(), kept
    for main()'s one-time startup call. Seeds Controls from what actually got
    built rather than from `loaded.affordability` alone - the identity check
    inside _reconcile_affordability is how the dashboard can refuse a switch
    to digits with a reason rather than quietly handing back brightness.

    Not for BotRunner: it must never build a Controls of its own (see
    build_checks()'s docstring) or a checks dict of its own (checks are
    per-process, not per-bot - the CPU cost of rebuilding the digit atlas on
    every Start would be silly, and the whole point of the split above is
    that restarting a bot must not re-run this).
    """
    checks = build_checks(atlas_root=atlas_root)
    controls = Controls(strategy=_reconcile_affordability(loaded, checks))
    return checks, controls




def build_shopping(
    bus: events.EventBus | None,
    templates: vision.TemplateCache | None,
    reader: digits.NumberReader | None = None,
    atlas_root: Path | None = None,
) -> ShoppingSession:
    """Build the shopping session, disabling it with a reason if this
    machine cannot read the screen at all.

    Always returns a *session* - never None. A None return would force
    every call site to branch before it could do anything, and TowerBot
    would need a null object anyway; this mirrors how build_affordability
    already degrades, handing back a working object of a lesser kind
    rather than nothing at all.

    The gate is the OCR engine (spec §10). Every visit re-reads the coin
    balance before it considers a row, and that read is OCR's now; a
    balance that comes back None aborts the visit outright, because
    shopping.py treats an unread number as "stop", never "guess". So an
    engine that will not import can never approve a purchase, and saying so
    once at startup beats a session that looks armed and silently declines
    forever. Prices still come off the glyph atlas until Phase 2 moves them
    too, which only widens what a missing engine costs.

    This used to gate on the header glyph atlas instead, which meant
    shopping stayed off until somebody played a session that pushed every
    missing digit through the coin balance by hand. Reading the header with
    OCR retired that requirement along with the harvesting - see
    shopping.header_numbers().
    """
    cache = digits.AtlasCache(
        atlas_root if atlas_root is not None else config.ATLAS_DIR
    )
    reader = reader if reader is not None else digits.NumberReader(cache)

    disabled_reason: str | None = None
    if not ocr.available():
        disabled_reason = "the OCR engine will not load"
        logger.warning(
            "shopping disabled: %s - see the traceback logged by tower_bot.ocr, "
            "and check that rapidocr_onnxruntime installed cleanly",
            disabled_reason,
        )

    return ShoppingSession(templates, bus, reader, disabled_reason=disabled_reason)


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
    stderr draws straight over the panel. No FAILURE is lost by silencing
    it: those reach the panel as BotError events on the bus. Log-only
    output is lost, though - including the Phase 1 OCR A/B on
    'tower_bot.ocr_ab', which has no event of its own - so the rehearsal in
    the Phase 1 plan must be run WITHOUT --tui.

    The name is in the format on purpose: the rehearsal greps the log for
    'ocr_ab', and a format that omits it turns "no disagreements found" and
    "the evidence was never written down" into the same empty grep.
    """
    if tui:
        logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()])
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)-20s %(message)s",
        datefmt="%H:%M:%S",
    )


def warn_if_web_host_exposed(host: str) -> None:
    """Nudge for the "reach it from my laptop" reflex.

    config.WEB_HOST's docstring carries the real warning, but nobody reads a
    default's docstring on the way to overriding it with --web-host. The
    dashboard serves live screenshots and full event history with no auth,
    and - now that the control plane is wired in - lets a caller start and
    stop the bot, rewrite what it buys, and create or delete strategy files
    on disk, so binding anything but loopback deserves pushback at the point
    someone is actually about to do it.
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
            "screenshots, event history, and control over the bot (start, "
            "stop, rewrite what it buys, create or delete strategy files on "
            "disk) will be reachable by anyone on this network, and there is "
            "no authentication.",
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


def apply_cli_overrides(
    store: StrategyStore, loaded: Strategy, args: argparse.Namespace
) -> Strategy:
    """Fold explicitly-passed flags into the loaded strategy, and save.

    Persisting is deliberate. The alternative - override without saving -
    reintroduces exactly the file-versus-live drift the strategy design pays
    to avoid, and does it where it is hardest to notice: the dashboard would
    show a value the file does not hold, with nothing on screen saying why.
    Persisting is occasionally surprising; drift is quietly wrong, and the
    log line below is what makes the surprise discoverable.

    Every one of these defaults to None in parse_args precisely so "not
    passed" and "passed the default value" are different here.
    """
    overrides = {
        "interval": args.interval,
        "auto_navigate": args.auto_navigate,
        "max_runs": args.max_runs,
        "affordability": args.affordability,
    }
    supplied = {key: value for key, value in overrides.items() if value is not None}
    if not supplied:
        return loaded

    updated = dataclasses.replace(loaded, **supplied)
    store.save(updated)
    logger.info(
        "Applied and saved CLI override(s) into strategy %r: %s",
        updated.name,
        ", ".join(f"{key}={value}" for key, value in sorted(supplied.items())),
    )
    return updated


def serve_web(
    runner: BotRunner,
    app: FastAPI,
    *,
    host: str,
    port: int,
    shutdown: threading.Event,
    start_immediately: bool = True,
) -> None:
    """Run the server on this thread, and a bot beside it on the runner's.

    Restructured from owning a worker thread to owning a BotRunner. The
    reasoning that shaped the old version all survives - it just moved:

    uvicorn installs its own SIGINT/SIGTERM handlers and can only do that
    from the main thread, so it still gets the main thread. The scan loop
    still runs on a worker, and they still genuinely run in parallel despite
    the GIL, because cv2.matchTemplate releases it for the duration of a
    match.

    `shutdown` is what `stop` used to be, narrowed: it means the PROCESS is
    going down, never merely the bot. It is still watched rather than only
    set, because the shutdown route has no handle on the Server; still set by
    _Server.handle_exit BEFORE graceful shutdown begins, so the SSE and MJPEG
    generators can end themselves inside its normal short path rather than
    waiting out the backstop; and still set in this function's finally, so a
    stream started after everything else ended is caught.

    What changed: a bot ending no longer ends the server. `--max-runs` now
    parks the dashboard with a stopped bot rather than exiting, which is the
    whole point - there is a Start button to press.

    No join here anymore, bounded or otherwise: `BotRunner.stop()` already
    does its own bounded join with a fixed timeout, so a second one here
    would just be redundant - and, worse, sized off a bot's `Controls` that
    under `--idle` before the first Start may not reflect anything the
    runner is actually running.
    """
    import uvicorn

    class _Server(uvicorn.Server):
        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            shutdown.set()
            super().handle_exit(sig, frame)

    config_ = uvicorn.Config(
        app, host=host, port=port, log_level="warning",
        # uvicorn's default here is None, which means "wait forever" for
        # in-flight responses - and an SSE feed or an MJPEG stream is
        # in-flight for as long as the tab is open. This is a BACKSTOP, not
        # the fix: the fix is `shutdown` being set before graceful shutdown
        # begins (see handle_exit above), so both generators end themselves
        # in the normal path. The 2s is what keeps a stream that somehow
        # missed the flag from hanging Ctrl+C indefinitely.
        timeout_graceful_shutdown=2,
    )
    server = _Server(config_)

    def _watch_shutdown() -> None:
        # The only waiter on `shutdown`. The route can set the flag but has
        # no other way to reach the runner or the Server.
        shutdown.wait()
        runner.stop()
        server.should_exit = True

    threading.Thread(target=_watch_shutdown, name="shutdown-watch", daemon=True).start()

    if start_immediately:
        try:
            runner.start()
        except RunnerError as exc:
            # Without --idle the user asked for a bot, so a device that is
            # not there is worth saying loudly - but not worth refusing to
            # serve over: the dashboard can show the error and offer Start.
            logger.error("%s - the dashboard is up; press Start to retry", exc)

    try:
        server.run()
    finally:
        shutdown.set()
        runner.stop()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.tui)

    # A dashboard (--web without --once, since --once always wins) connects
    # lazily instead: the device becomes the runner's device_factory below,
    # so a dead emulator surfaces as a 503 from /api/bot/start rather than
    # `main()` refusing to serve at all. Every other path - --once,
    # plain logging, --tui - has no dashboard to report a failure into, so
    # it still connects eagerly and fails fast the way it always has.
    # --debug-scores is one of those: it captures a frame and exits before
    # anything ever serves, with or without --web, so it always needs a
    # device up front - deferring it here would only trade a clean
    # "no emulator" error for capture_screen(None) blowing up below.
    serving = args.web and not args.once and not args.debug_scores
    device = None
    if not serving:
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
    # The PROCESS going down, not the bot - see runner.BotRunner for that
    # half. serve_web() and event_stream() watch this separately from
    # uvicorn's own should_exit so a held-open dashboard tab is told too.
    shutdown = threading.Event()

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
        where = f"http://{args.web_host}:{args.web_port}"
        if args.idle:
            print(f"Dashboard on {where} - no bot running, press Start")
        else:
            print(f"Dashboard on {where}")

    # Everything from start() onwards is inside the try: anything raising
    # between starting the consumer threads and the loop would otherwise
    # leave them running and, under --tui, leave rich's Live holding the
    # terminal.
    try:
        for sink in sinks:
            sink.start()

        if device is not None:
            # Nothing to verify yet under a lazy device: the runner connects
            # (or doesn't) once start() actually runs, well after this point.
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

        store = StrategyStore()
        try:
            loaded = store.load(args.strategy) if args.strategy else store.ensure_seeded()
        except ControlError as exc:
            # Every profile on disk failed to parse - almost always one
            # hand-edited file with a trailing comma. Same treatment as a
            # missing emulator: say which directory to look in and exit,
            # rather than dumping a traceback the owner has to decode.
            logger.error(
                "%s - fix or delete the offending file in %s", exc, store.directory
            )
            return 1
        loaded = apply_cli_overrides(store, loaded, args)

        checks, controls = build_checks_and_controls(loaded)
        # checks[controls.snapshot().strategy.affordability] is never None
        # here: build_checks_and_controls() already seeded controls' strategy
        # to an affordability name whose check built (falling back to
        # "brightness" itself when it did not), so a "checks[...] or
        # checks['brightness']" fallback would be dead code.
        loaded_affordability = controls.snapshot().strategy.affordability
        # Built once, here, for the same reason `checks` is: the header
        # glyph atlas it gates on is exactly as expensive to build as the
        # digit atlas `checks` already amortises across every bot this
        # process ever starts.
        shopping_session = build_shopping(bus, vision.TemplateCache(config.TEMPLATE_DIR))

        if args.once:
            # A single scan never settles the debounced tracker (it needs
            # SCREEN_CONFIRMATIONS consecutive identical readings), so a
            # lone run_once() would always report UNKNOWN even when the
            # game is clearly on GAME_OVER at 0.998. Scan enough times to
            # settle so --once actually names the real screen.
            bot = TowerBot(
                device=device,
                templates=vision.TemplateCache(config.TEMPLATE_DIR),
                bus=bus,
                affordability_check=checks[loaded_affordability],
                controls=controls,
                checks=checks,
                shopping=shopping_session,
                first_run_id=last_run + 1,
                frames=frames,
            )
            install_signal_handlers(bot)
            for _ in range(config.SCREEN_CONFIRMATIONS):
                bot.run_once()
        elif args.web:
            warn_if_web_host_exposed(args.web_host)

            from web.app import create_app

            runner = BotRunner(
                bus=bus,
                controls=controls,
                state=state,
                templates=vision.TemplateCache(config.TEMPLATE_DIR),
                device_factory=lambda: connect_device(host=args.host, port=args.port),
                checks=checks,
                shopping=shopping_session,
                frames=frames,
                first_run_id=last_run + 1,
            )

            app = create_app(
                state=state, sse=sse, bus=bus,
                db_path=db_path if args.store else None,
                shutdown=shutdown,
                controls=controls,
                checks=checks,
                frames=frames,
                runner=runner,
                store=store,
                shopping=shopping_session,
            )
            # No signal handlers of ours here: uvicorn installs its own and
            # would overwrite them anyway.
            serve_web(
                runner, app, host=args.web_host, port=args.web_port,
                shutdown=shutdown, start_immediately=not args.idle,
            )
        else:
            bot = TowerBot(
                device=device,
                templates=vision.TemplateCache(config.TEMPLATE_DIR),
                bus=bus,
                affordability_check=checks[loaded_affordability],
                controls=controls,
                checks=checks,
                shopping=shopping_session,
                first_run_id=last_run + 1,
                frames=frames,
            )
            install_signal_handlers(bot)
            # No explicit interval, so run_forever re-reads
            # bot.controls.snapshot().strategy.interval every iteration -
            # the loaded strategy is the one source of truth for the pace
            # even without --web. --max-runs is passed explicitly too: it
            # wins over the strategy's own max_runs when set (see
            # run_cap_reached's docstring), and apply_cli_overrides has
            # already folded and saved it into the strategy either way, so
            # the two never actually disagree.
            bot.run_forever(max_runs=args.max_runs)
    finally:
        for sink in sinks:
            sink.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
