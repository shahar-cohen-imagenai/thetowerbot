# BotRunner and the lifecycle split — Implementation Plan (3 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the dashboard outlive the bot — start, stop and restart a bot from the browser, with `--web --idle` serving an empty dashboard that waits for Start.

**Architecture:** A new `runner.py` owns the device, the `TowerBot` and the worker thread behind a lock. The single `stop` Event that today means both "stop the bot" and "shut the process down" splits into a process-level `shutdown` (which the SSE and MJPEG generators watch, exactly as they watch `stop` now) and a runner-internal per-bot stop. `serve_web()` stops owning a worker thread and owns a runner instead.

**Tech Stack:** Python 3.12, threading, FastAPI, uvicorn, `pytest`, `uv run`.

**Spec:** `docs/superpowers/specs/2026-09-02-bot-lifecycle-and-strategy-design.md` (sections 7, 8, 10 and 13)

**Depends on:** plans 1 and 2. `Strategy`, `StrategyStore`, and the reshaped `Controls` must exist.

## Global Constraints

- Every function and method carries type hints; `from __future__ import annotations` at the top of every module.
- `runner.py` may import `tower_bot`, `control`, `strategy`, `events` and `config`. It must NOT import `web/`. `web/app.py` imports `runner`, never the other way round.
- Run tests with `uv run pytest <file>` and `-p no:allure_pytest`. Never run the whole suite.
- No test in this plan may require a real emulator. Every runner test injects a fake device factory.
- `--web` without `--idle` must behave exactly as it does today: serve and start scanning. This is the compatibility guarantee — if it changes, the plan is wrong.

---

## File Structure

| File | Responsibility |
|---|---|
| `runner.py` (create) | `BotRunner` — device, bot, worker thread, `start`/`stop`/`status` |
| `runs.py` (modify) | expose `RunTracker.next_id` so run ids survive a restart |
| `sinks/state.py` (modify) | `BotState.reset()` |
| `tower_bot.py` (modify) | `serve_web` owns a runner; `--idle` and `--strategy` flags; CLI-flag persistence |
| `web/app.py` (modify) | lifecycle and strategy routes; `stop` renamed to `shutdown` |
| `tests/test_runner.py` (create) | start/stop/restart, run-id continuity, device failure |
| `tests/test_lifecycle_api.py` (create) | the new routes and their status codes |
| `tests/test_strategies_api.py` (create) | the strategy CRUD routes |
| `tools/smoke_dashboard.py` (modify) | the shutdown-route check, and a new bot-stop-leaves-server-up check |

---

### Task 1: Run-id continuity and `BotState.reset()`

Two small enabling changes, taken together because neither is worth its own review and both exist for the same reason: something that was per-process is now per-bot.

**Files:**
- Modify: `runs.py` (`RunTracker`, around line 32-42)
- Modify: `sinks/state.py` (`BotState`, around line 26-40)
- Test: `tests/test_runs.py` (append), `tests/test_state.py` (append)

**Interfaces:**
- Produces: `RunTracker.next_id -> int` (read-only property), `BotState.reset() -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runs.py`:

```python
def test_next_id_is_readable_so_a_restart_can_carry_it_forward() -> None:
    """A second bot in one process must not reissue the first bot's run ids.

    prepare_store() seeds the id once, at launch. Once the dashboard can
    start several bots without relaunching, the runner has to carry the
    counter across - and it needs a public way to read it.
    """
    from runs import RunTracker
    from screens import ScreenState

    tracker = RunTracker(start_id=5)
    assert tracker.next_id == 5
    tracker.transition(ScreenState.IN_RUN, now=0.0)
    assert tracker.current_id == 5
    assert tracker.next_id == 6


def test_a_fresh_tracker_seeded_from_next_id_does_not_collide() -> None:
    from runs import RunTracker
    from screens import ScreenState

    first = RunTracker(start_id=1)
    first.transition(ScreenState.IN_RUN, now=0.0)
    first.transition(ScreenState.GAME_OVER, now=1.0)

    second = RunTracker(start_id=first.next_id)
    second.transition(ScreenState.IN_RUN, now=2.0)
    assert second.current_id == 2
```

Append to `tests/test_state.py`:

```python
def test_reset_clears_the_previous_bot_s_accumulators() -> None:
    """BotState outlives a bot now, so its counters must be resettable.

    Without this the status bar shows a stopped bot's scan count beside a
    fresh bot's uptime, which reads as one impossibly slow session.
    """
    import time

    import events
    from sinks.state import BotState

    state = BotState()
    state.apply(events.ScanCompleted(seq=1, ts=time.time(), screen="IN_RUN", duration_ms=1.0))
    state.apply(events.Tapped(seq=2, ts=time.time(), action="Damage", x=1, y=2, score=0.9))
    state.apply(events.BotError(seq=3, ts=time.time(), message="boom"))
    assert state.snapshot()["scans"] == 1

    state.reset()
    after = state.snapshot()
    assert after["scans"] == 0
    assert after["taps"] == {}
    assert after["last_error"] is None
    assert after["run"] is None
    assert after["uptime"] < 1.0


def test_reset_is_safe_while_events_are_arriving() -> None:
    import threading
    import time

    import events
    from sinks.state import BotState

    state = BotState()
    stop = threading.Event()

    def feed() -> None:
        while not stop.is_set():
            state.apply(
                events.ScanCompleted(seq=1, ts=time.time(), screen="IN_RUN", duration_ms=1.0)
            )

    thread = threading.Thread(target=feed)
    thread.start()
    try:
        for _ in range(100):
            state.reset()
            state.snapshot()
    finally:
        stop.set()
        thread.join()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_runs.py tests/test_state.py -q -p no:allure_pytest -k "next_id or collide or reset"`
Expected: FAIL with `AttributeError: 'RunTracker' object has no attribute 'next_id'`

- [ ] **Step 3: Add `RunTracker.next_id`**

In `runs.py`, add directly after `__init__`:

```python
    @property
    def next_id(self) -> int:
        """The id the next run will take.

        Public so a restarting BotRunner can seed the next tracker from the
        last one. prepare_store() seeds this from the database at launch, but
        the database is only re-read at launch - and the dashboard can now
        start several bots between two launches.
        """
        return self._next_id
```

- [ ] **Step 4: Add `BotState.reset()`**

In `sinks/state.py`, add after `__init__`, setting every field `__init__` sets:

```python
    def reset(self) -> None:
        """Forget the previous bot. Called by BotRunner on every start.

        BotState now outlives any individual bot, because the dashboard does.
        Without this, uptime, scans and the tap tallies accumulate across bot
        lifetimes and the status bar shows a stopped bot's scan count beside
        a fresh bot's uptime.
        """
        with self._lock:
            self.screen = "UNKNOWN"
            self.scans = 0
            self.started = time.monotonic()
            self.runs_completed = 0
            self.run_id = None
            self.run_started = None
            self.run_taps = Counter()
            self.wallet = None
            self.last_error = None
            self.taps = Counter()
            self.skips = Counter()
            self.tail.clear()
```

The implementer must read `sinks/state.py`'s `__init__` first and reset exactly the fields it initialises — the list above is from the current file, and any field it has that is missing here is a bug. Use `grep -n "self\." sinks/state.py | head -30` to confirm.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_runs.py tests/test_state.py -q -p no:allure_pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add runs.py sinks/state.py tests/test_runs.py tests/test_state.py
git commit -m "feat: expose RunTracker.next_id and add BotState.reset

Both exist for the same reason: the dashboard is about to outlive the
bot, so a counter that was per-process becomes per-bot. Run ids must
carry across a restart or the second session overwrites the first's rows;
BotState must not, or the status bar mixes two bots' numbers."
```

---

### Task 2: `BotRunner`

**Files:**
- Create: `runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `tower_bot.TowerBot`, `control.Controls`, `strategy.Strategy`, `events.EventBus`, `events.BotError`, `sinks.state.BotState`, `frames.FrameBuffer`, `vision.TemplateCache`
- Produces:
  - `runner.BotRunner(bus, controls, state, templates, device_factory, checks, frames=None, first_run_id=1)`
  - `.start() -> dict[str, Any]` — raises `runner.RunnerError` on refusal
  - `.stop(timeout: float = 7.0) -> dict[str, Any]`
  - `.status() -> dict[str, Any]` → `{"running": bool, "since": float | None, "error": str | None}`
  - `runner.RunnerError(Exception)` with `.status_code: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner.py`:

```python
"""The thing the browser's Start button reaches.

Every test here injects a fake device factory: the runner's job is
lifecycle, and requiring a real emulator to test lifecycle would mean it
never got tested. The bot itself is faked too where the test is about
start/stop rather than about scanning.
"""

from __future__ import annotations

import threading
import time

import pytest

from control import Controls
from events import EventBus
from runner import BotRunner, RunnerError
from sinks.state import BotState
from strategy import ActionRule, Strategy


class FakeBot:
    """Stands in for TowerBot: spins until stopped, and counts its runs."""

    def __init__(self, first_run_id: int = 1) -> None:
        self.runs = type("R", (), {"next_id": first_run_id, "completed": 0})()
        self._stopping = threading.Event()
        self.scans = 0

    def run_forever(self, **kwargs) -> None:
        while not self._stopping.wait(0.001):
            self.scans += 1

    def stop(self, *_: object) -> None:
        self._stopping.set()


def a_strategy(**overrides) -> Strategy:
    base = dict(
        name="test",
        actions=(ActionRule(name="Damage", template="upgrade_damage.png"),),
    )
    return Strategy(**{**base, **overrides})


@pytest.fixture
def runner_parts():
    """A runner over fakes, plus the pieces a test needs to inspect."""
    made: list[FakeBot] = []
    devices: list[object] = []

    def device_factory():
        device = object()
        devices.append(device)
        return device

    def bot_factory(*, device, first_run_id, **kwargs):
        bot = FakeBot(first_run_id=first_run_id)
        made.append(bot)
        return bot

    bus = EventBus()
    seen: list = []
    bus.subscribe(type("R", (), {"offer": lambda self, e: seen.append(e) or True})())
    state = BotState()
    runner = BotRunner(
        bus=bus,
        controls=Controls(strategy=a_strategy()),
        state=state,
        templates=object(),
        device_factory=device_factory,
        checks={"brightness": object(), "digits": None},
        bot_factory=bot_factory,
    )
    return runner, made, devices, seen, state


def test_a_fresh_runner_is_not_running(runner_parts) -> None:
    runner, _, _, _, _ = runner_parts
    status = runner.status()
    assert status["running"] is False
    assert status["since"] is None
    assert status["error"] is None


def test_start_connects_a_device_and_spawns_a_bot(runner_parts) -> None:
    runner, made, devices, _, _ = runner_parts
    status = runner.start()
    try:
        assert status["running"] is True
        assert status["since"] is not None
        assert len(devices) == 1
        assert len(made) == 1
    finally:
        runner.stop()


def test_stop_ends_the_worker(runner_parts) -> None:
    runner, made, _, _, _ = runner_parts
    runner.start()
    status = runner.stop()
    assert status["running"] is False
    # The thread is genuinely gone, not merely flagged as stopped.
    assert not runner._thread.is_alive()


def test_starting_twice_is_refused_rather_than_silently_ignored(runner_parts) -> None:
    """The browser asked for something that did not happen. Saying so beats
    a 200 that means nothing."""
    runner, made, _, _, _ = runner_parts
    runner.start()
    try:
        with pytest.raises(RunnerError) as caught:
            runner.start()
        assert caught.value.status_code == 409
        assert len(made) == 1
    finally:
        runner.stop()


def test_stopping_a_stopped_runner_is_harmless(runner_parts) -> None:
    # Idempotent on purpose: a double-click on Stop, or a Stop racing the
    # bot hitting max_runs, must not raise.
    runner, _, _, _, _ = runner_parts
    assert runner.stop()["running"] is False
    assert runner.stop()["running"] is False


def test_run_ids_carry_across_a_restart(runner_parts) -> None:
    """The failure this exists to stop: session 2's run 2 overwriting
    session 1's run 2 in the runs table."""
    runner, made, _, _, _ = runner_parts
    runner.start()
    made[0].runs.next_id = 12  # the first bot completed some runs
    runner.stop()

    runner.start()
    try:
        assert made[1].runs.next_id == 12
    finally:
        runner.stop()


def test_start_resets_bot_state_but_not_the_bus(runner_parts) -> None:
    import events as ev

    runner, _, _, seen, state = runner_parts
    state.apply(ev.ScanCompleted(seq=1, ts=time.time(), screen="IN_RUN", duration_ms=1.0))
    before_seq = runner._bus.publish(ev.Navigated(target="BATTLE")).seq

    runner.start()
    try:
        assert state.snapshot()["scans"] == 0
        # The bus is deliberately NOT reset: seq must keep climbing so a
        # reconnecting browser's Last-Event-ID stays meaningful across a
        # restart, which is exactly when you are watching.
        assert runner._bus.publish(ev.Navigated(target="BATTLE")).seq > before_seq
    finally:
        runner.stop()


def test_a_dead_emulator_is_reported_not_raised_out_of_the_process(runner_parts) -> None:
    """Today a bad device returns exit 1 from main() before anything serves.
    In idle mode it must not take the dashboard down with it."""
    runner, _, _, seen, _ = runner_parts

    def broken():
        from device import EmulatorError

        raise EmulatorError("no emulator at 127.0.0.1:5555")

    runner._device_factory = broken
    with pytest.raises(RunnerError) as caught:
        runner.start()
    assert caught.value.status_code == 503
    assert "no emulator" in str(caught.value)

    assert runner.status()["running"] is False
    assert runner.status()["error"] is not None
    # And it lands in the feed and on the errors page like every other failure.
    assert any(e.type == "BotError" for e in seen)


def test_the_error_clears_on_a_successful_start(runner_parts) -> None:
    runner, _, _, _, _ = runner_parts

    def broken():
        from device import EmulatorError

        raise EmulatorError("nope")

    original = runner._device_factory
    runner._device_factory = broken
    with pytest.raises(RunnerError):
        runner.start()
    assert runner.status()["error"] is not None

    runner._device_factory = original
    runner.start()
    try:
        assert runner.status()["error"] is None
    finally:
        runner.stop()


def test_a_bot_that_ends_on_its_own_leaves_the_runner_stopped(runner_parts) -> None:
    """max_runs reached. The server keeps serving; only the bot ended."""
    runner, _, _, _, _ = runner_parts

    def ends_immediately(*, device, first_run_id, **kwargs):
        bot = FakeBot(first_run_id=first_run_id)
        bot.run_forever = lambda **k: None
        return bot

    runner._bot_factory = ends_immediately
    runner.start()
    for _ in range(200):
        if not runner.status()["running"]:
            break
        time.sleep(0.01)
    assert runner.status()["running"] is False


def test_concurrent_starts_spawn_exactly_one_bot(runner_parts) -> None:
    runner, made, _, _, _ = runner_parts
    refused: list[RunnerError] = []

    def racer() -> None:
        try:
            runner.start()
        except RunnerError as exc:
            refused.append(exc)

    threads = [threading.Thread(target=racer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert len(made) == 1
        assert len(refused) == 7
    finally:
        runner.stop()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_runner.py -q -p no:allure_pytest`
Expected: collection error — `ModuleNotFoundError: No module named 'runner'`

- [ ] **Step 3: Write runner.py**

Create `runner.py`:

```python
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
spawn a thread. Imports the bot, never the web layer.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Any, Callable

import events
from control import Controls
from device import EmulatorError

logger = logging.getLogger(__name__)


class RunnerError(Exception):
    """A lifecycle request that could not be honoured.

    Carries the HTTP status the route should return, so the web layer maps
    one exception rather than distinguishing several.
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _default_bot_factory(**kwargs: Any):
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
        state: Any,
        templates: Any,
        device_factory: Callable[[], Any],
        checks: dict[str, Any],
        frames: Any = None,
        first_run_id: int = 1,
        bot_factory: Callable[..., Any] = _default_bot_factory,
    ) -> None:
        self._bus = bus
        self._controls = controls
        self._state = state
        self._templates = templates
        self._device_factory = device_factory
        self._checks = checks
        self._frames = frames
        self._bot_factory = bot_factory

        self._lock = threading.Lock()
        self._bot: Any | None = None
        self._thread: threading.Thread | None = None
        self._since: float | None = None
        self._error: str | None = None
        # Seeded from the database once, at launch. Carried forward from each
        # bot's own tracker after that - see _harvest().
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

            # A fresh bot's counters start at zero; the state sink's must too,
            # or the status bar mixes this bot's uptime with the last one's
            # scan count. The BUS is deliberately not reset - seq keeps
            # climbing so a reconnecting browser resumes across the restart.
            self._state.reset()

            strategy = self._controls.snapshot().strategy
            bot = self._bot_factory(
                device=device,
                templates=self._templates,
                bus=self._bus,
                controls=self._controls,
                checks=self._checks,
                frames=self._frames,
                first_run_id=self._next_run_id,
                screen_confirmations=strategy.screen_confirmations,
                navigation_cooldown=strategy.navigation_cooldown,
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
        """
        with self._lock:
            bot, thread = self._bot, self._thread
            if bot is None or thread is None:
                self._reap_locked()
                return {"running": False, "since": None, "error": self._error}

        bot.stop()
        thread.join(timeout=timeout)
        with self._lock:
            if thread.is_alive():
                # Daemon thread, so it cannot keep the process alive - but a
                # loop that ignored stop() is worth saying out loud.
                logger.warning("the scan loop did not stop within %.1fs", timeout)
            self._harvest_locked(bot)
            self._reap_locked()
            return {"running": False, "since": None, "error": self._error}
```

- [ ] **Step 4: Add the two start-only parameters to `TowerBot.__init__`**

`BotRunner` passes `screen_confirmations` and `navigation_cooldown`. `TowerBot.__init__` must accept them and thread them into the two stateful collaborators. In `tower_bot.py`:

```python
        screen_confirmations: int = config.SCREEN_CONFIRMATIONS,
        navigation_cooldown: float = config.NAVIGATION_COOLDOWN_SECONDS,
```

and in the body:

```python
        # Read once, here, rather than per scan: both configure an object
        # that carries state across scans (the tracker's part-confirmed
        # reading, the navigator's last-navigation timestamp), and changing
        # either under a running one has no correct answer. This is the
        # "applies on next Start" boundary the dashboard labels.
        self.tracker = screens.ScreenTracker(confirmations=screen_confirmations)
        self.navigator = Navigator(templates, bus, cooldown=navigation_cooldown)
```

Check `screens.ScreenTracker.__init__` and `navigate.Navigator.__init__` first:

Run: `grep -n "def __init__" -A 6 screens.py navigate.py`

If either does not take that parameter, add it with the `config` constant as its default — a defaulted parameter keeps every existing caller working.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_runner.py -q -p no:allure_pytest`
Expected: PASS

- [ ] **Step 6: Run the tests for everything that builds a bot**

Run: `uv run pytest tests/test_gating.py tests/test_bot_reporting.py tests/test_screen_tracker.py tests/test_navigate.py -q -p no:allure_pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add runner.py tower_bot.py screens.py navigate.py tests/test_runner.py
git commit -m "feat: BotRunner owns the bot's lifetime

The device connection moves into start(), which is the whole difference:
a dead emulator becomes a 503 and a BotError on the feed rather than
exit 1 from a process that never served anything.

Run ids are harvested from each bot's tracker on the way out, so a second
start does not overwrite the first session's rows. BotState is reset on
start; the EventBus deliberately is not."
```

---

### Task 3: The flag split and the lifecycle routes

**Files:**
- Modify: `web/app.py` — `create_app` signature, the two stream routes, the control block
- Test: `tests/test_lifecycle_api.py` (create)

**Interfaces:**
- Consumes: `runner.BotRunner`, `runner.RunnerError`
- Produces: `create_app(..., shutdown: threading.Event | None = None, runner: BotRunner | None = None)`; routes `POST /api/bot/start`, `POST /api/bot/stop`, `POST /api/shutdown`; `GET /api/status` gains a `bot` key

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lifecycle_api.py`:

```python
"""Start, stop, and shut down - three verbs where there was one.

The old POST /api/control/stop meant both "stop the bot" and "shut the
process down". Once the dashboard outlives the bot those are different
things, and one route cannot mean both.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

import config
from control import Controls
from events import EventBus
from runner import RunnerError
from sinks.sse import SseSink
from sinks.state import BotState
from strategy import ActionRule, Strategy
from web.app import create_app


class FakeRunner:
    """Records what the routes asked for, without any threads."""

    def __init__(self) -> None:
        self.running = False
        self.starts = 0
        self.stops = 0
        self.fail_with: RunnerError | None = None

    def start(self) -> dict:
        self.starts += 1
        if self.fail_with is not None:
            raise self.fail_with
        self.running = True
        return {"running": True, "since": 1.0, "error": None}

    def stop(self) -> dict:
        self.stops += 1
        self.running = False
        return {"running": False, "since": None, "error": None}

    def status(self) -> dict:
        return {"running": self.running, "since": 1.0 if self.running else None,
                "error": None}


@pytest.fixture
def wired():
    runner = FakeRunner()
    shutdown = threading.Event()
    controls = Controls(strategy=Strategy(
        name="test",
        actions=(ActionRule(name="Damage", template="upgrade_damage.png"),),
    ))
    app = create_app(
        state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None,
        unknown_dir=config.UNKNOWN_DIR, shutdown=shutdown,
        controls=controls, checks={"brightness": object(), "digits": None},
        runner=runner,
    )
    return TestClient(app), runner, shutdown


def test_start_starts_the_bot_and_not_the_shutdown(wired) -> None:
    client, runner, shutdown = wired
    body = client.post("/api/bot/start")
    assert body.status_code == 200
    assert body.json()["running"] is True
    assert runner.starts == 1
    assert not shutdown.is_set()


def test_starting_a_running_bot_is_a_409(wired) -> None:
    client, runner, _ = wired
    runner.fail_with = RunnerError("already running", 409)
    response = client.post("/api/bot/start")
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


def test_a_dead_emulator_is_a_503_not_a_500(wired) -> None:
    client, runner, _ = wired
    runner.fail_with = RunnerError("no emulator at 127.0.0.1:5555", 503)
    response = client.post("/api/bot/start")
    assert response.status_code == 503
    assert "emulator" in response.json()["detail"]


def test_stopping_the_bot_leaves_the_server_up(wired) -> None:
    """The whole point of the split. Stop must not kill the dashboard you
    pressed it from."""
    client, runner, shutdown = wired
    client.post("/api/bot/start")
    response = client.post("/api/bot/stop")
    assert response.status_code == 200
    assert response.json()["running"] is False
    assert runner.stops == 1
    assert not shutdown.is_set()
    # Still serving.
    assert client.get("/api/status").status_code == 200


def test_shutdown_sets_the_process_flag(wired) -> None:
    client, _, shutdown = wired
    assert client.post("/api/shutdown").json() == {"stopping": True}
    assert shutdown.is_set()


def test_status_reports_the_bot_block(wired) -> None:
    client, _, _ = wired
    assert client.get("/api/status").json()["bot"]["running"] is False
    client.post("/api/bot/start")
    assert client.get("/api/status").json()["bot"]["running"] is True


def test_status_has_a_bot_block_even_with_no_runner() -> None:
    """--once, --tui and every test predating this pass no runner. The key
    must still be there, so the browser needs no special case."""
    app = create_app(
        state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None,
        unknown_dir=config.UNKNOWN_DIR,
    )
    body = TestClient(app).get("/api/status").json()
    assert body["bot"] == {"running": False, "since": None, "error": None}


def test_the_lifecycle_routes_are_absent_without_a_runner() -> None:
    app = create_app(
        state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None,
        unknown_dir=config.UNKNOWN_DIR,
    )
    client = TestClient(app)
    # 404 from the static mount, not a 405 or a 500: the route never existed.
    assert client.post("/api/bot/start").status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_lifecycle_api.py -q -p no:allure_pytest`
Expected: FAIL — `create_app() got an unexpected keyword argument 'shutdown'`

- [ ] **Step 3: Rename `stop` to `shutdown` throughout `web/app.py`**

This is a pure rename with one narrowing of meaning. In `web/app.py`:

- `create_app`'s `stop: threading.Event | None = None` parameter becomes `shutdown: threading.Event | None = None`, with the local `if stop is None` block renamed to match.
- `event_stream`'s and `frame_stream`'s `stop` parameters keep their names (they are local to those generators and their docstrings are already correct) but the call sites pass `stop=shutdown`.
- Update `create_app`'s docstring: the flag now means "the process is going down", and the bot's own stop is the runner's.

Update the module docstring's warning to match section 13 of the spec:

```python
Binds 127.0.0.1. No auth, /api/unknown serves screenshots of a live session,
and the control plane lets a caller start and stop the bot, rewrite what it
buys, and create or delete strategy files on disk - see the warning beside
config.WEB_HOST before changing the bind address.

"The web layer never writes" is no longer true in general, and the narrower
claim is the one that matters: it never writes to the DATABASE. db.reader()
opens mode=ro and nothing here can change that. Strategy files are the one
thing it does write, through StrategyStore, which validates every profile
before it reaches the disk.
```

- [ ] **Step 4: Add the `bot` block to `/api/status`**

In the `status()` route, before the `return`:

```python
        # Always present, even with no runner (--once, --tui, and every test
        # predating the lifecycle split), so the browser needs no special case
        # for "this build cannot start a bot".
        payload["bot"] = (
            runner.status()
            if runner is not None
            else {"running": False, "since": None, "error": None}
        )
```

- [ ] **Step 5: Add the lifecycle routes**

Inside `create_app`, after the existing `if controls is not None:` block, add:

```python
    if runner is not None:

        @app.post("/api/bot/start")
        def start_bot() -> dict:
            try:
                return runner.start()
            except RunnerError as exc:
                # The runner already published a BotError for anything worth
                # seeing in the feed; this is just the caller's answer.
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        @app.post("/api/bot/stop")
        def stop_bot() -> dict:
            return runner.stop()

    @app.post("/api/shutdown")
    def shutdown_all() -> dict:
        # This route has no handle on the worker thread or the Server - the
        # shared `shutdown` Event is the only thing it can reach from a
        # request handler. serve_web()'s watcher thread is the one actually
        # waiting on it: it stops the runner and sets server.should_exit,
        # which brings the loop and the server down together.
        shutdown.set()
        return {"stopping": True}
```

Delete the old `POST /api/control/stop` route entirely, and add `from runner import BotRunner, RunnerError` plus the `runner: BotRunner | None = None` parameter to `create_app`.

`/api/shutdown` is registered unconditionally — a dashboard that cannot shut itself down is worse than one that can, and it needs no runner to do it.

- [ ] **Step 6: Run the lifecycle tests**

Run: `uv run pytest tests/test_lifecycle_api.py -q -p no:allure_pytest`
Expected: PASS

- [ ] **Step 7: Fix the existing web tests for the rename**

Run: `uv run pytest tests/test_web_api.py tests/test_control_api.py tests/test_frame_api.py tests/test_stats_api.py -q -p no:allure_pytest`

Every call passing `stop=` to `create_app` becomes `shutdown=`. Any test asserting on `POST /api/control/stop` moves to `/api/shutdown`. Expected after the fixes: PASS.

- [ ] **Step 8: Commit**

```bash
git add web/app.py tests/test_lifecycle_api.py tests/test_web_api.py tests/test_control_api.py tests/test_frame_api.py tests/test_stats_api.py
git commit -m "feat: split stop into a bot stop and a process shutdown

One Event meant both, which is why Start was impossible: pressing Stop
killed the dashboard you pressed it from. /api/bot/stop ends the bot;
/api/shutdown ends the process. The stream generators watch the latter,
which is exactly what they watched before under its old name."
```

---

### Task 4: Strategy CRUD routes

**Files:**
- Modify: `web/app.py`
- Test: `tests/test_strategies_api.py` (create)

**Interfaces:**
- Consumes: `strategy.StrategyStore`, `strategy.Strategy`, `strategy.ControlError`, `Controls.replace()`
- Produces: `create_app(..., store: StrategyStore | None = None)`; routes `GET/PUT/DELETE /api/strategies[/{name}]`, `POST /api/strategies/{name}/activate`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategies_api.py`:

```python
"""Named strategies over HTTP.

The store is the source of truth for what exists; Controls is the source of
truth for what is running. Activation is the one operation that touches both.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from control import Controls
from events import EventBus
from sinks.sse import SseSink
from sinks.state import BotState
from strategy import Strategy, StrategyStore
from web.app import create_app


@pytest.fixture
def wired(tmp_path, monkeypatch):
    templates = tmp_path / "templates"
    templates.mkdir()
    for action in config.ACTIONS:
        (templates / action.template).write_bytes(b"")
    monkeypatch.setattr(config, "TEMPLATE_DIR", templates)

    store = StrategyStore(tmp_path / "strategies")
    active = store.ensure_seeded()
    controls = Controls(strategy=active)
    seen: list = []
    bus = EventBus()
    bus.subscribe(type("R", (), {"offer": lambda self, e: seen.append(e) or True})())
    app = create_app(
        state=BotState(), sse=SseSink(), bus=bus, db_path=None,
        unknown_dir=config.UNKNOWN_DIR, controls=controls,
        checks={"brightness": object(), "digits": object()}, store=store,
    )
    return TestClient(app), store, controls, seen


def test_listing_names_the_active_one(wired) -> None:
    client, _, _, _ = wired
    body = client.get("/api/strategies").json()
    assert body == {"active": "default", "names": ["default"]}


def test_reading_one_returns_the_whole_strategy(wired) -> None:
    client, _, _, _ = wired
    body = client.get("/api/strategies/default").json()
    assert body["name"] == "default"
    assert len(body["actions"]) == len(config.ACTIONS)


def test_reading_an_absent_one_is_a_404(wired) -> None:
    client, _, _, _ = wired
    assert client.get("/api/strategies/ghost").status_code == 404


def test_a_traversal_name_is_refused_not_resolved(wired) -> None:
    client, _, _, _ = wired
    # Starlette normalises some of these before routing, so any non-2xx is a
    # pass; what must never happen is a 200 carrying a file from outside.
    for name in ("..", "%2e%2e%2fetc%2fpasswd", "has space"):
        assert client.get(f"/api/strategies/{name}").status_code >= 400


def test_putting_a_new_strategy_creates_it(wired) -> None:
    client, store, _, _ = wired
    body = client.get("/api/strategies/default").json()
    body["name"] = "crit"
    body["interval"] = 4.0
    response = client.put("/api/strategies/crit", json=body)
    assert response.status_code == 200
    assert store.load("crit").interval == 4.0
    assert client.get("/api/strategies").json()["names"] == ["crit", "default"]


def test_the_url_name_wins_over_the_body_name(wired) -> None:
    """Otherwise PUT /api/strategies/a with a body naming "b" writes b.json,
    and the caller has no way to know where their profile went."""
    client, store, _, _ = wired
    body = client.get("/api/strategies/default").json()
    body["name"] = "somethingelse"
    client.put("/api/strategies/crit", json=body)
    assert "crit" in store.names()
    assert "somethingelse" not in store.names()


def test_an_invalid_strategy_is_a_422_naming_the_field(wired) -> None:
    client, store, _, _ = wired
    body = client.get("/api/strategies/default").json()
    body["interval"] = 0
    response = client.put("/api/strategies/broken", json=body)
    assert response.status_code == 422
    assert "interval" in response.json()["detail"]
    assert "broken" not in store.names()


def test_putting_the_active_strategy_applies_it_live(wired) -> None:
    """Source of truth means one truth: a saved edit to the running profile
    must reach the loop, not wait for a restart."""
    client, _, controls, seen = wired
    body = client.get("/api/strategies/default").json()
    body["interval"] = 7.0
    client.put("/api/strategies/default", json=body)
    assert controls.snapshot().strategy.interval == 7.0
    assert any(e.type == "ControlChanged" for e in seen)


def test_putting_an_inactive_strategy_does_not_touch_the_loop(wired) -> None:
    client, _, controls, _ = wired
    body = client.get("/api/strategies/default").json()
    body["name"] = "crit"
    body["interval"] = 7.0
    client.put("/api/strategies/crit", json=body)
    assert controls.snapshot().strategy.interval != 7.0


def test_activating_loads_it_and_persists_the_pointer(wired) -> None:
    client, store, controls, seen = wired
    body = client.get("/api/strategies/default").json()
    body["name"] = "crit"
    body["interval"] = 6.0
    client.put("/api/strategies/crit", json=body)

    response = client.post("/api/strategies/crit/activate")
    assert response.status_code == 200
    assert controls.snapshot().strategy.name == "crit"
    assert controls.snapshot().strategy.interval == 6.0
    assert store.active_name() == "crit"
    assert client.get("/api/strategies").json()["active"] == "crit"
    assert any(e.type == "ControlChanged" for e in seen)


def test_activating_an_absent_strategy_is_a_404(wired) -> None:
    client, _, controls, _ = wired
    assert client.post("/api/strategies/ghost/activate").status_code == 404
    assert controls.snapshot().strategy.name == "default"


def test_deleting_a_spare_strategy_works(wired) -> None:
    client, store, _, _ = wired
    body = client.get("/api/strategies/default").json()
    body["name"] = "crit"
    client.put("/api/strategies/crit", json=body)
    assert client.delete("/api/strategies/crit").status_code == 200
    assert store.names() == ["default"]


def test_deleting_the_active_strategy_is_a_409(wired) -> None:
    client, store, _, _ = wired
    body = client.get("/api/strategies/default").json()
    body["name"] = "crit"
    client.put("/api/strategies/crit", json=body)
    response = client.delete("/api/strategies/default")
    assert response.status_code == 409
    assert "default" in store.names()


def test_deleting_the_last_strategy_is_a_409(wired) -> None:
    client, store, _, _ = wired
    response = client.delete("/api/strategies/default")
    assert response.status_code == 409
    assert store.names() == ["default"]


def test_a_patch_persists_into_the_active_profile(wired) -> None:
    """The promise of "strategy is the source of truth": an edit that
    applied live but did not persist would leave the file and the loop
    disagreeing until the next explicit save."""
    client, store, _, _ = wired
    client.patch("/api/control", json={"interval": 8.0})
    assert store.load("default").interval == 8.0


def test_a_rejected_patch_writes_nothing(wired) -> None:
    client, store, _, _ = wired
    before = store.load("default").interval
    assert client.patch("/api/control", json={"interval": 0}).status_code == 422
    assert store.load("default").interval == before


def test_the_strategy_routes_are_absent_without_a_store() -> None:
    app = create_app(
        state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None,
        unknown_dir=config.UNKNOWN_DIR,
    )
    assert TestClient(app).get("/api/strategies").status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_strategies_api.py -q -p no:allure_pytest`
Expected: FAIL — `create_app() got an unexpected keyword argument 'store'`

- [ ] **Step 3: Add the routes**

Add `store: StrategyStore | None = None` to `create_app`'s signature and `from strategy import ControlError as StrategyError, Strategy, StrategyStore` to its imports (the existing `from control import ControlError` already covers the same class — they are the same object after plan 2's re-export, so import it once and use one name).

Inside `create_app`, after the lifecycle block:

```python
    if store is not None:

        def _persist_active(strategy_: Strategy) -> None:
            """Write the running policy back to its file.

            This is what "one source of truth" costs. An edit that applied
            live but did not persist would leave the file and the loop
            disagreeing until the next explicit save - the exact drift this
            design exists to remove.
            """
            store.save(strategy_)

        @app.get("/api/strategies")
        def list_strategies() -> dict:
            return {"active": store.active_name(), "names": store.names()}

        @app.get("/api/strategies/{name}")
        def read_strategy(name: str) -> dict:
            try:
                return store.load(name).to_dict()
            except ControlError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        @app.put("/api/strategies/{name}")
        def write_strategy(name: str, body: dict[str, Any]) -> dict:
            # The URL's name wins. Otherwise a body naming something else
            # writes a different file and the caller has no way to know
            # where their profile went.
            try:
                incoming = Strategy.from_dict({**body, "name": name})
                store.save(incoming)
            except ControlError as exc:
                raise HTTPException(
                    status_code=422, detail=f"{exc.field}: {exc}"
                ) from exc

            # Saving the profile the bot is currently running IS a live edit.
            if controls is not None and name == store.active_name():
                changed = controls.replace(incoming)
                if changed:
                    bus.publish(events.ControlChanged(changed=changed, source="web"))
            return incoming.to_dict()

        @app.post("/api/strategies/{name}/activate")
        def activate_strategy(name: str) -> dict:
            try:
                loaded = store.load(name)
                store.set_active(name)
            except ControlError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if controls is not None:
                changed = controls.replace(loaded)
                if changed:
                    bus.publish(events.ControlChanged(changed=changed, source="web"))
            return {"active": name, "names": store.names()}

        @app.delete("/api/strategies/{name}")
        def remove_strategy(name: str) -> dict:
            try:
                store.delete(name)
            except ControlError as exc:
                # 409, not 404: the profile exists, and the refusal is about
                # what deleting it would leave behind.
                code = 404 if "no strategy named" in str(exc) else 409
                raise HTTPException(status_code=code, detail=str(exc)) from exc
            return {"active": store.active_name(), "names": store.names()}
```

- [ ] **Step 4: Make `PATCH /api/control` persist**

In `patch_control`, after the successful `controls.apply(...)` and before the `ControlChanged` publish, add:

```python
            if changed and store is not None:
                # A patch to the running policy is a save. See _persist_active.
                _persist_active(controls.snapshot().strategy)
```

`_persist_active` is defined inside the `if store is not None:` block, so hoist it above the `if controls is not None:` block — or, simpler, inline `store.save(controls.snapshot().strategy)` here with the comment. The implementer should pick whichever keeps `create_app` readable and note the choice in the commit.

- [ ] **Step 5: Run the strategy API tests**

Run: `uv run pytest tests/test_strategies_api.py -q -p no:allure_pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add web/app.py tests/test_strategies_api.py
git commit -m "feat: strategy CRUD over HTTP, and PATCH persists

Saving the profile the bot is running IS a live edit, and a PATCH to the
running policy IS a save - that is what one source of truth costs. An
edit that applied live but did not persist would leave the file and the
loop disagreeing until the next explicit save.

The URL's name wins over the body's, so a PUT cannot write a file the
caller did not ask for."
```

---

### Task 5: `serve_web`, `--idle` and `--strategy`

**Files:**
- Modify: `tower_bot.py` — `parse_args`, `serve_web`, `main`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `runner.BotRunner`, `strategy.StrategyStore`
- Produces: `serve_web(runner, app, *, host, port, shutdown, start_immediately=True)`; CLI flags `--idle`, `--strategy NAME`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_idle_and_strategy_flags_parse() -> None:
    from tower_bot import parse_args

    args = parse_args(["--web", "--idle", "--strategy", "crit"])
    assert args.idle is True
    assert args.strategy == "crit"


def test_idle_defaults_off_so_web_still_means_serve_and_scan() -> None:
    """The compatibility guarantee. If this fails, every existing invocation
    changed behaviour."""
    from tower_bot import parse_args

    assert parse_args(["--web"]).idle is False


def test_overlapping_flags_default_to_none_so_unset_is_distinguishable() -> None:
    """A flag that was not passed must not look like a flag set to the
    argparse default - otherwise it would overwrite the loaded strategy with
    a value nobody asked for."""
    from tower_bot import parse_args

    args = parse_args([])
    assert args.interval is None
    assert args.max_runs is None
    assert args.affordability is None
    assert args.auto_navigate is None


def test_apply_cli_overrides_persists_only_what_was_passed(tmp_path, monkeypatch) -> None:
    import config
    from strategy import Strategy, StrategyStore
    from tower_bot import apply_cli_overrides, parse_args

    templates = tmp_path / "templates"
    templates.mkdir()
    for action in config.ACTIONS:
        (templates / action.template).write_bytes(b"")
    monkeypatch.setattr(config, "TEMPLATE_DIR", templates)

    store = StrategyStore(tmp_path / "strategies")
    store.ensure_seeded()

    result = apply_cli_overrides(store, store.load("default"), parse_args(["--interval", "9"]))
    assert result.interval == 9.0
    # Persisted, not merely applied: one truth, always. See the spec's
    # section 10 for why this beats override-without-persist.
    assert store.load("default").interval == 9.0
    # And nothing else moved.
    assert store.load("default").auto_navigate is False


def test_apply_cli_overrides_writes_nothing_when_no_flag_was_passed(
    tmp_path, monkeypatch
) -> None:
    import config
    from strategy import StrategyStore
    from tower_bot import apply_cli_overrides, parse_args

    templates = tmp_path / "templates"
    templates.mkdir()
    for action in config.ACTIONS:
        (templates / action.template).write_bytes(b"")
    monkeypatch.setattr(config, "TEMPLATE_DIR", templates)

    store = StrategyStore(tmp_path / "strategies")
    loaded = store.ensure_seeded()
    before = store.path_for("default").read_text()

    assert apply_cli_overrides(store, loaded, parse_args([])) == loaded
    assert store.path_for("default").read_text() == before
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -p no:allure_pytest -k "idle or overlapping or overrides"`
Expected: FAIL with `AttributeError: 'Namespace' object has no attribute 'idle'`

- [ ] **Step 3: Change the overlapping flags to `None` defaults**

In `parse_args`, change four arguments so an unset flag is distinguishable from a defaulted one:

```python
    parser.add_argument(
        "--interval", type=float, default=None,
        help=(
            "seconds between scans - overrides the active strategy AND is "
            "saved into it"
        ),
    )
    parser.add_argument(
        "--auto-navigate", action="store_true", default=None,
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
```

and add the two new ones:

```python
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
```

- [ ] **Step 4: Add `apply_cli_overrides`**

Add to `tower_bot.py`, above `main`:

```python
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

    updated = replace(loaded, **supplied)
    store.save(updated)
    logger.info(
        "Applied and saved CLI override(s) into strategy %r: %s",
        updated.name,
        ", ".join(f"{key}={value}" for key, value in sorted(supplied.items())),
    )
    return updated
```

- [ ] **Step 5: Rewrite `serve_web` to own a runner**

Replace `serve_web`'s signature and body. The uvicorn setup, `_Server.handle_exit`, and `timeout_graceful_shutdown` are unchanged — only what the worker is changes.

```python
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
    """
    import uvicorn

    class _Server(uvicorn.Server):
        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            shutdown.set()
            super().handle_exit(sig, frame)

    config_ = uvicorn.Config(
        app, host=host, port=port, log_level="warning",
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
```

Add `from runner import BotRunner, RunnerError` to `tower_bot.py`'s imports.

- [ ] **Step 6: Rewire `main`**

In `main()`, replace the device connection and bot construction for the `--web` path. The key changes:

- `connect_device` is no longer called eagerly when `args.web` is set. It becomes the runner's `device_factory`: `lambda: connect_device(host=args.host, port=args.port)`. The non-web paths (`--once`, plain logging, `--tui`) still connect eagerly and fail fast, because they have no dashboard to report into.
- Load the strategy before building anything: `store = StrategyStore()`, `loaded = store.load(args.strategy) if args.strategy else store.ensure_seeded()`, then `loaded = apply_cli_overrides(store, loaded, args)`.
- `build_checks_and_controls(loaded, ...)` per plan 2.
- Build the runner, pass it and the store to `create_app`, and call `serve_web(runner, app, host=..., port=..., shutdown=stop, start_immediately=not args.idle)`.
- Rename the local `stop` to `shutdown` to match.
- The `print(f"Dashboard on ...")` line gains an idle note:

```python
    if args.web and not args.once:
        where = f"http://{args.web_host}:{args.web_port}"
        if args.idle:
            print(f"Dashboard on {where} - no bot running, press Start")
        else:
            print(f"Dashboard on {where}")
```

- [ ] **Step 7: Verify the CLI tests pass**

Run: `uv run pytest tests/test_cli.py -q -p no:allure_pytest`
Expected: PASS

- [ ] **Step 8: Smoke-test both modes by hand**

```bash
# Idle: serves, no bot, no emulator needed.
uv run tower_bot.py --web --idle &
sleep 2
curl -s localhost:8765/api/status | python3 -m json.tool | head -20
curl -s localhost:8765/api/strategies
curl -s -X POST localhost:8765/api/bot/start   # 503 with no emulator, and that is correct
curl -s -X POST localhost:8765/api/shutdown
```

Expected: `bot.running` is `false`; `/api/strategies` lists `default`; Start returns a 503 naming the emulator (unless one is actually running); shutdown ends the process. **The 503 is the pass condition, not a failure** — it proves a dead emulator no longer takes the dashboard down.

- [ ] **Step 9: Update the dashboard smoke script**

`tools/smoke_dashboard.py` breaks in three places, and it is the script that
exists to catch exactly this kind of regression — so it has to move with the
change rather than after it.

Run: `grep -n "stop\|controls\.interval\|api/control" tools/smoke_dashboard.py`

Three fixes:

1. `create_app(..., stop=stop, ...)` becomes `shutdown=shutdown`, and the
   local variable is renamed to match.
2. `check_stop_route_stops_serve_web()` asserted that `POST /api/control/stop`
   brings `serve_web` down. That route is gone. Point it at `/api/shutdown`
   and rename it `check_shutdown_route_stops_serve_web` — the behaviour it
   guards (a browser's request actually stopping the loop *and* the server,
   rather than only killing the SSE feed so the dashboard merely *looks*
   stopped) is unchanged and still worth guarding.
3. Its fake bot exposes `controls.interval` for `serve_web`'s bounded join.
   `serve_web` no longer joins a thread itself — `BotRunner.stop()` does, with
   a fixed timeout — so the fake needs a `stop()` and a `status()` and should
   stand in for the *runner*, not the bot. Replace it with the `FakeRunner`
   shape from `tests/test_lifecycle_api.py`, plus whatever `serve_web` calls
   on it (`start`, `stop`).

Add one new check while you are in there, because it is the single most
valuable thing this script can assert about the split:

```python
def check_bot_stop_leaves_the_server_serving(tmp: Path) -> None:
    """The whole point of splitting one flag into two.

    Before, one Event meant both "stop the bot" and "shut the process down",
    so a browser pressing Stop killed the dashboard it was pressed from.
    Assert the server still answers after the bot has stopped.
    """
    # Build the app with a FakeRunner and a shutdown Event, POST
    # /api/bot/stop, then GET /api/status and require a 200 with
    # bot.running False and the shutdown Event still clear.
```

- [ ] **Step 10: Run the smoke script**

Run: `uv run python tools/smoke_dashboard.py`
Expected: every check passes. This script needs no emulator — it drives the
app with fakes.

- [ ] **Step 11: Update the README**

Three edits:

- The `--web` row in the flags table gains `--idle` and `--strategy` beneath it.
- The "five pages" sentence stays five for now — plan 4 adds the sixth page and changes it.
- The security warning gains the new powers: "start a bot, rewrite what it buys, and create or delete strategy files".
- A new short section under **Run** describing `strategies/`: one JSON file per profile, committed, hand-editable, `.active` names the current one.

- [ ] **Step 12: Commit**

```bash
git add tower_bot.py tests/test_cli.py tools/smoke_dashboard.py README.md
git commit -m "feat: --idle serves a dashboard with no bot, --strategy picks a profile

serve_web owns a BotRunner rather than a worker thread. A bot ending no
longer ends the server, so --max-runs parks the dashboard with a stopped
bot instead of exiting - which is the point, there is a Start button.

CLI flags that duplicate strategy fields now override the loaded profile
and persist into it, with a log line. Override-without-persist would
reintroduce the file-versus-live drift the design pays to avoid."
```

---

## Self-Review Notes

**Spec coverage.** Section 7 is Tasks 1-3 (the runner, all three "things it must get right", and the flag-split table). Section 8's routes are Tasks 3-4, including the `PATCH`-persists rule. Section 10 is Task 5. Section 13's docstring and README changes are Task 3 step 3 and Task 5 step 9.

**The compatibility guarantee has a test.** `test_idle_defaults_off_so_web_still_means_serve_and_scan` is the one that fails loudly if `--web` changes meaning.

**Risk concentrated in Task 5.** It is the only task that cannot be fully unit-tested — `serve_web` binds a port and runs uvicorn. Step 8's manual smoke test is deliberate, and its pass condition is stated explicitly because "a 503" reads like a failure to someone skimming.

**Type consistency.** `BotRunner.start()`/`.stop()`/`.status()` all return `{"running", "since", "error"}`, which is what `FakeRunner` returns in `tests/test_lifecycle_api.py`, what `/api/status`'s `bot` key carries, and what plan 4's `StatBar` pill reads. `RunnerError.status_code` is set at every raise site (409, 503) and read in exactly one place, `start_bot()`.
