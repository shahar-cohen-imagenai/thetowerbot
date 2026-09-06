"""Owns the bot's lifetime, so the dashboard does not have to share it.

Until now the scan loop and the web server lived and died together: one
`stop` Event brought both down, which is what kept Ctrl+C, --max-runs and
the browser's Stop button on a single shutdown path. That coupling also
meant a browser could never START a bot, because by the time anything was
serving, the bot was already running.

This is the thing that breaks the tie. It owns the device connection, the
TowerBot and the worker thread; the server owns this. A bot can now end -
by Stop, or by reaching its run cap - without the server ending, and a new
one can be started in its place.

Everything here is guarded by one lock, so two concurrent starts cannot both
spawn a thread. Imports the bot, never the web layer - `frames` and
`sinks.state` are shared plumbing the scan loop writes to, not part of the
dashboard, so naming their types below costs nothing the bot did not already
pay (device.py pulls cv2 in regardless).
"""

from __future__ import annotations

from account_collection import StatsCollection
from missions_claim import MissionsClaim
from missions_screen import MissionsReadings
from missions_visit import MissionsVisit
from account_state import AccountState

import logging
import threading
import time
import traceback
from typing import Any, Callable

import events
import vision
from autopilot import AutopilotState
from control import Controls
from device import EmulatorError
from frames import FrameBuffer
from sinks.state import BotState

logger = logging.getLogger(__name__)


class RunnerError(Exception):
    """A lifecycle request that could not be honoured.

    Carries the HTTP status the route should return, so the web layer maps
    one exception rather than distinguishing several.
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _default_bot_factory(**kwargs: Any) -> Any:
    """Imported lazily: tower_bot imports control, and importing it at module
    scope here would drag the whole bot into any process that only wanted the
    runner's types."""
    from tower_bot import TowerBot

    return TowerBot(**kwargs)


class BotRunner:
    """Start, stop and report on one bot at a time."""

    def __init__(
        self,
        *,
        bus: events.EventBus,
        controls: Controls,
        state: BotState,
        templates: vision.TemplateCache,
        device_factory: Callable[[], Any],
        checks: dict[str, Any],
        frames: FrameBuffer | None = None,
        first_run_id: int = 1,
        bot_factory: Callable[..., Any] = _default_bot_factory,
        shopping: Any | None = None,
        account_state: AccountState | None = None,
    ) -> None:
        self._bus = bus
        self._controls = controls
        self._state = state
        self._templates = templates
        self._device_factory = device_factory
        self._checks = checks
        self._frames = frames
        self._bot_factory = bot_factory
        # Built once per process by tower_bot.build_shopping(), the same way
        # `checks` is - see that function's docstring. Reused for every bot
        # this runner ever starts rather than rebuilt per Start, because
        # rebuilding the header glyph atlas on every Start would make the
        # button slow for no gain.
        self._shopping = shopping
        self.autopilot_state = AutopilotState()
        self.account_state = account_state or AccountState()
        # One transaction object for the runner's whole lifetime, handed to
        # every bot it starts - the same reason account_state is shared. The
        # browser arms it here; the scan loop is what walks it.
        self.collection = StatsCollection()
        # The Missions visit and the reader it takes its arrival evidence
        # from, owned here for the same reason and on the same terms.
        self.visit = MissionsVisit()
        # The claim walk, owned on the same terms as the read-only visit: one
        # instance, cancelled by a restart or a pause, never queued.
        self.claim = MissionsClaim()
        self.missions = MissionsReadings()

        self._lock = threading.Lock()
        self._bot: Any | None = None
        self._thread: threading.Thread | None = None
        self._since: float | None = None
        self._error: str | None = None
        self._device_serial: str | None = None
        # Seeded from the database once, at launch. Carried forward from each
        # bot's own tracker after that - see _harvest_locked().
        self._next_run_id = first_run_id

    # -- reporting ---------------------------------------------------------
    def _running_locked(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._running_locked()
            return {
                "running": running,
                "since": self._since if running else None,
                "error": self._error,
            }

    def identity(self) -> dict[str, str | None]:
        """Identity already learned during start; this never performs ADB I/O."""
        with self._lock:
            return {"serial": self._device_serial, "game_version": None}

    def request_autopilot(self, command: dict[str, Any]) -> None:
        with self._lock:
            if not self._running_locked() or self._bot is None:
                raise RunnerError("Start the bot before sending commands", 409)
            if self._controls.snapshot().paused or self._bot.screen_state.value != "IN_RUN":
                raise RunnerError("Manual upgrades require an unpaused battle", 409)
            try:
                self._bot.autopilot.submit(command)
            except ValueError as exc:
                raise RunnerError(str(exc), 409) from None

    def request_stats_collection(self) -> dict[str, Any]:
        """Arm one read-only Home -> Settings -> Stats -> Home transaction.

        Every refusal here is about evidence the caller cannot supply
        later: a stopped or paused loop would never walk the steps, a
        battle is the wrong screen to leave, and a reader that could not
        load cannot identify the panels the steps must verify. None of
        them queues - a command the loop cannot honour now is refused, not
        banked, exactly as request_autopilot refuses.
        """
        with self._lock:
            if not self._running_locked() or self._bot is None:
                raise RunnerError("Start the bot before collecting stats", 409)
            unavailable = getattr(self._shopping, "disabled_reason", None)
            if unavailable:
                raise RunnerError(f"Stats collection is unavailable: {unavailable}", 503)
            if self._controls.snapshot().paused:
                raise RunnerError("Collecting stats requires an unpaused bot", 409)
            if self._bot.screen_state.value != "MAIN_MENU":
                raise RunnerError("Collecting stats requires a confirmed main menu", 409)
            if self.visit.active:
                raise RunnerError("A missions visit is already walking the menus", 409)
            if self.claim.active:
                raise RunnerError("A missions claim is already walking the menus", 409)
            if not self.collection.request():
                raise RunnerError("A stats collection is already running", 409)
            return self.collection.snapshot()

    def request_missions_visit(self) -> dict[str, Any]:
        """Arm one read-only Home -> Missions -> read -> Home visit.

        The same refusals as request_stats_collection, for the same reason:
        a command the loop cannot honour NOW is refused rather than banked.
        Two transactions may never be armed at once - they would walk the
        same menus from different steps, and the second one's evidence would
        be the first one's screen.
        """
        with self._lock:
            if not self._running_locked() or self._bot is None:
                raise RunnerError("Start the bot before visiting missions", 409)
            unavailable = getattr(self._shopping, "disabled_reason", None)
            if unavailable:
                raise RunnerError(f"Missions visits are unavailable: {unavailable}", 503)
            if self._controls.snapshot().paused:
                raise RunnerError("Visiting missions requires an unpaused bot", 409)
            if self._bot.screen_state.value != "MAIN_MENU":
                raise RunnerError("Visiting missions requires a confirmed main menu", 409)
            if self.collection.active:
                raise RunnerError("A stats collection is already walking the menus", 409)
            if self.claim.active:
                raise RunnerError("A missions claim is already walking the menus", 409)
            if not self.visit.request():
                raise RunnerError("A missions visit is already running", 409)
            return self.visit.snapshot()

    def request_missions_claim(self) -> dict[str, Any]:
        """Arm one Home -> Missions -> claim -> Home walk.

        The same refusals as request_missions_visit, and one more object to
        refuse against: three transactions now walk the same menus, and any
        two of them armed at once would each read the other's screen as its
        own evidence.

        Unlike its two siblings this walk TAPS things that change the
        account, so the refusals are load-bearing rather than tidy.
        """
        with self._lock:
            if not self._running_locked() or self._bot is None:
                raise RunnerError("Start the bot before claiming missions", 409)
            unavailable = getattr(self._shopping, "disabled_reason", None)
            if unavailable:
                raise RunnerError(f"Mission claims are unavailable: {unavailable}", 503)
            if self._controls.snapshot().paused:
                raise RunnerError("Claiming missions requires an unpaused bot", 409)
            if self._bot.screen_state.value != "MAIN_MENU":
                raise RunnerError("Claiming missions requires a confirmed main menu", 409)
            if self.collection.active:
                raise RunnerError("A stats collection is already walking the menus", 409)
            if self.visit.active:
                raise RunnerError("A missions visit is already walking the menus", 409)
            if not self.claim.request():
                raise RunnerError("A missions claim is already running", 409)
            return self.claim.snapshot()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> dict[str, Any]:
        """Connect, build a bot, spawn the worker. Raises RunnerError.

        The device connection happens HERE rather than at launch, which is
        the whole difference: a dead emulator becomes a 503 and a BotError on
        the feed, not exit 1 from a process that never served anything.
        """
        with self._lock:
            if self._running_locked():
                raise RunnerError("the bot is already running", 409)
            # Reap a finished thread before reusing the slot, so a bot that
            # ended on its own does not block the next start.
            self._reap_locked()

            try:
                device = self._device_factory()
            except EmulatorError as exc:
                self._error = str(exc)
                # Published outside the lock would be tidier, but publish()
                # never blocks (see events.EventBus) so holding it is safe.
                self._bus.publish(events.BotError(message=str(exc)))
                raise RunnerError(str(exc), 503) from None
            except Exception as exc:  # noqa: BLE001 - anything else is still fatal to a start
                self._error = str(exc)
                self._bus.publish(
                    events.BotError(message=str(exc), traceback=traceback.format_exc())
                )
                raise RunnerError(str(exc), 503) from None

            self._device_serial = getattr(device, "serial", None)

            # A fresh bot's counters start at zero; the state sink's must too,
            # or the status bar mixes this bot's uptime with the last one's
            # scan count. The BUS is deliberately not reset - seq keeps
            # climbing so a reconnecting browser resumes across the restart.
            #
            # Known and accepted: this reset is not synchronised with
            # StateSink's consumer queue. A fast Stop-then-Start can reset
            # the state while the previous bot's last few events are still
            # queued, and those then land on the NEW bot's counters and
            # inflate them for as long as it runs (nothing recomputes them).
            # Left alone deliberately - the damage is display-only, it needs
            # a restart inside one queue drain to happen at all, and draining
            # the sink here would put a cross-thread handshake on the Start
            # path to tidy up a scan count.
            self._state.reset()
            self.account_state.reset_confirmation()
            self.account_state.screen_readings.reset_current()
            # A transaction half-walked by the previous bot must not resume
            # under a new one: its remembered step assumes a panel the
            # emulator may no longer be showing. Cancelling keeps the failure
            # visible instead of silently rearming.
            self.collection.cancel(
                "bot_restarted",
                "A new bot replaced the one walking this transaction; it was not resumed.",
            )
            self.visit.cancel(
                "bot_restarted",
                "A new bot replaced the one walking this visit; it was not resumed.",
            )
            self.autopilot_state.clear_battle()
            self.autopilot_state.decision("idle", "Waiting for a fresh battle observation")

            # A visit left mid-errand by the previous bot (Stop pressed
            # mid-visit, or run_forever ending because max_runs was lowered
            # from the dashboard while shopping was under way) must not
            # resume against stale state under a NEW bot: `_step` would
            # still be non-IDLE, so the new bot's very first run_once() would
            # call advance() directly - skipping begin() entirely, and with
            # it the enabled check, the cadence, the run cap and the
            # MAIN_MENU precondition - while the emulator may not even be on
            # the page that stale `_categories` list assumes. reset() forces
            # IDLE with no return tap of its own; if the emulator is still
            # sitting on a menu page, the new bot's own begin()/advance()
            # cycle deals with that from a clean slate exactly as it would
            # after any other restart.
            if self._shopping is not None:
                self._shopping.reset()

            strategy = self._controls.snapshot().strategy
            bot = self._bot_factory(
                device=device,
                templates=self._templates,
                bus=self._bus,
                controls=self._controls,
                checks=self._checks,
                shopping=self._shopping,
                frames=self._frames,
                first_run_id=self._next_run_id,
                screen_confirmations=strategy.screen_confirmations,
                navigation_cooldown=strategy.navigation_cooldown,
                autopilot_state=self.autopilot_state,
                account_state=self.account_state,
                collection=self.collection,
                visit=self.visit,
                claim=self.claim,
                missions=self.missions,
            )

            self._bot = bot
            self._error = None
            self._since = time.time()
            self._thread = threading.Thread(
                target=self._run, args=(bot,), name="scan-loop", daemon=True
            )
            self._thread.start()
            return {
                "running": True,
                "since": self._since,
                "error": None,
            }

    def _run(self, bot: Any) -> None:
        try:
            bot.run_forever()
        except Exception as exc:  # noqa: BLE001 - a crashed loop must not be silent
            logger.exception("the scan loop stopped with an error")
            self._bus.publish(
                events.BotError(message=str(exc), traceback=traceback.format_exc())
            )
            with self._lock:
                self._error = str(exc)
        finally:
            # Whether it ended by Stop, by its run cap, or by raising, the
            # next bot must not reissue this one's run ids.
            with self._lock:
                self.account_state.screen_readings.reset_current()
                self.collection.cancel(
                    "bot_stopped",
                    "The scan loop ended before the transaction finished.",
                )
                self.visit.cancel(
                    "bot_stopped",
                    "The scan loop ended before the visit finished.",
                )
                self._harvest_locked(bot)

    def _harvest_locked(self, bot: Any) -> None:
        try:
            self._next_run_id = max(self._next_run_id, bot.runs.next_id)
        except AttributeError:
            pass

    def _reap_locked(self) -> None:
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None
            self._bot = None
            self._since = None

    def stop(self, timeout: float = 7.0) -> dict[str, Any]:
        """End the current bot. Idempotent, and never brings the server down.

        Bounded join: bot.stop() interrupts the between-scan wait immediately
        whatever the interval, so this only ever waits out a scan already in
        flight. It must NOT be sized to the interval itself - the browser can
        set that as high as MAX_INTERVAL, and a Stop must not inherit an hour
        as its deadline.

        Deliberately does NOT reap `_thread`/`_bot` here: `status()` already
        reports "running": False the instant the thread is no longer alive
        (see `_running_locked()`), so nothing depends on clearing them early,
        and a caller inspecting the thread right after stop() - as a test
        proving it is genuinely gone, not merely flagged - should still find
        it. The slot is reclaimed lazily, by the next start()'s own
        `_reap_locked()` call.

        The join is bounded, so the thread is NOT guaranteed to be gone when
        this returns, and the answer is derived from `_running_locked()` for
        exactly that reason rather than hardcoded to False. Reporting
        "stopped" after a join that timed out would contradict the very
        thing the timeout just proved: `status()` would keep saying running
        (same predicate), the next `start()` would refuse with 409, and the
        bot would still be tapping the game while the browser was told it
        had stopped. A honest "running": True says instead that the request
        was made and the loop has not honoured it yet.
        """
        with self._lock:
            bot, thread = self._bot, self._thread
            if bot is None or thread is None:
                self._reap_locked()
                return {"running": False, "since": None, "error": self._error}

        bot.stop()
        thread.join(timeout=timeout)
        with self._lock:
            running = self._running_locked()
            if running:
                # Daemon thread, so it cannot keep the process alive - but a
                # loop that ignored stop() is worth saying out loud.
                logger.warning("the scan loop did not stop within %.1fs", timeout)
            self._harvest_locked(bot)
            return {
                "running": running,
                "since": self._since if running else None,
                "error": self._error,
            }
