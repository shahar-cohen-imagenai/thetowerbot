# Dashboard Rewrite, Plan B — The Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the browser pause, resume and stop the bot, and change its scan interval, auto-navigate, affordability strategy and enabled actions while it runs.

**Architecture:** One lock-protected `Controls` dataclass in a new top-level `control.py`, shaped like the existing `BotState`: one lock, one `snapshot()`, no mutable objects shared across threads. The scan loop takes exactly one snapshot at the top of each pass. Three HTTP routes read and patch it, and every accepted change publishes a `ControlChanged` event onto the bus, so a setting change is as reconstructable afterwards as every other state change. The web layer still cannot write to the database — `db.reader()` stays `mode=ro`; what it now writes to is one in-memory dataclass.

**Tech Stack:** Python 3.12, FastAPI, Pydantic (already a FastAPI dependency), pytest. UI side: Next.js/React/TypeScript from Plan A. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-01-tower-bot-dashboard-ui-design.md` (§4)

**Depends on:** Plan A (`2026-09-01-dashboard-a-scaffold.md`) must be complete — Task 6 here adds a page to the Next.js app and requires `web/ui/lib/api.ts`.

## Global Constraints

- Python >= 3.12. Type hints on every function. `from __future__ import annotations` at the top of every Python module.
- Python dependencies via `uv add`; node dependencies via `npm install` inside `web/ui/`. Never hand-edit `pyproject.toml`.
- `control.py` lives at the **top level**, beside `db.py` and `events.py` — not inside `web/`. The scan loop depends on it and the scan loop must never import the web layer.
- No route may write to the database. `db.reader()` keeps opening `mode=ro`.
- `bus.publish()` must never block. Control publishes through it like everything else.
- The dashboard binds loopback. Do not add authentication.
- After any UI change, `npm run build` in `web/ui` and commit `web/static/` — the freshness test from Plan A Task 4 fails otherwise.
- Commit after every task.

## Design decisions locked in here

**`Controls` replaces `TowerBot`'s `auto_navigate` parameter.** Two sources of truth for the same setting is the bug this plan would otherwise ship. Task 3 removes the constructor parameter and updates the tests that pass it.

**Affordability checks are built once at startup, never in a request handler.** `TowerBot` holds a `dict[str, AffordabilityCheck | None]` keyed by strategy name. A missing atlas makes `checks["digits"]` `None`, which is exactly the condition Task 4 turns into a 422 — so a browser asking for digits with no atlas is told, rather than silently given brightness.

## File Structure

| Path | Responsibility |
|---|---|
| `control.py` | the `Controls` dataclass, its lock, `snapshot()` and `apply()` |
| `events.py` | modify: add `ControlChanged` |
| `tower_bot.py` | modify: loop reads `Controls`; `main()` builds it from args |
| `web/app.py` | modify: `GET`/`PATCH` `/api/control`, `POST /api/control/stop` |
| `web/ui/lib/api.ts` | modify: `fetchControl`, `patchControl`, `stopBot` |
| `web/ui/app/control/page.tsx` | the control page |
| `tests/test_control.py` | `Controls` semantics |
| `tests/test_control_api.py` | the three routes |
| `tests/test_gating.py` | modify: pause and enabled-actions behaviour in the loop |

---

### Task 1: The Controls dataclass

**Files:**
- Create: `control.py`
- Test: `tests/test_control.py`

**Interfaces:**
- Produces: `Controls(paused, interval, auto_navigate, strategy, enabled_actions)` with `snapshot() -> dict[str, Any]` and `apply(patch: Mapping[str, Any]) -> dict[str, Any]` returning **only the fields that actually changed**. Raises `ControlError(field, message)` on an invalid patch.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_control.py`:

```python
"""Controls is the one mutable thing the web layer may touch."""

from __future__ import annotations

import threading

import pytest

from control import ControlError, Controls


def test_snapshot_is_json_safe_and_detached() -> None:
    controls = Controls(enabled_actions={"Damage"})
    snap = controls.snapshot()
    assert isinstance(snap["enabled_actions"], list)
    # Mutating the snapshot must not reach back into the live object: the
    # whole point of snapshotting is that the loop and the web thread never
    # share a mutable structure.
    snap["enabled_actions"].append("Tier")
    assert controls.snapshot()["enabled_actions"] == ["Damage"]


def test_apply_returns_only_what_changed() -> None:
    controls = Controls(paused=False, interval=2.0)
    changed = controls.apply({"paused": True, "interval": 2.0})
    assert changed == {"paused": True}
    assert controls.snapshot()["paused"] is True


def test_apply_ignores_unknown_fields() -> None:
    controls = Controls()
    assert controls.apply({"nonsense": 1}) == {}


def test_interval_must_be_positive_and_sane() -> None:
    controls = Controls(interval=2.0)
    with pytest.raises(ControlError) as caught:
        controls.apply({"interval": 0})
    assert caught.value.field == "interval"
    # A rejected patch changes nothing at all.
    assert controls.snapshot()["interval"] == 2.0

    with pytest.raises(ControlError):
        controls.apply({"interval": 3601})


def test_strategy_must_be_known() -> None:
    controls = Controls()
    with pytest.raises(ControlError) as caught:
        controls.apply({"strategy": "vibes"})
    assert caught.value.field == "strategy"


def test_enabled_actions_must_be_a_list_of_strings() -> None:
    controls = Controls(enabled_actions={"Damage"})
    assert controls.apply({"enabled_actions": ["Damage", "Tier"]}) == {
        "enabled_actions": ["Damage", "Tier"]
    }
    with pytest.raises(ControlError):
        controls.apply({"enabled_actions": "Damage"})


def test_a_partial_patch_that_fails_late_changes_nothing() -> None:
    """Validate everything, then commit - never half-apply."""
    controls = Controls(paused=False, interval=2.0)
    with pytest.raises(ControlError):
        controls.apply({"paused": True, "interval": -1})
    assert controls.snapshot()["paused"] is False


def test_concurrent_applies_do_not_corrupt_state() -> None:
    controls = Controls(interval=2.0)
    errors: list[Exception] = []

    def hammer(value: float) -> None:
        try:
            for _ in range(200):
                controls.apply({"interval": value})
                controls.snapshot()
        except Exception as exc:  # noqa: BLE001 - the test is what it catches
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(v,)) for v in (1.0, 5.0)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert controls.snapshot()["interval"] in (1.0, 5.0)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_control.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control'`.

- [ ] **Step 3: Write `control.py`**

```python
"""What the browser is allowed to change while the bot runs.

Shaped like BotState on purpose: one lock, one snapshot() that hands out a
detached copy, and no mutable structure ever shared across the boundary. The
scan loop reads a snapshot once per pass; the web layer patches through
apply(). Neither ever holds the other's objects.

This module is deliberately at the top level rather than under web/: the scan
loop depends on it, and the scan loop must not import the web layer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

import config

STRATEGIES = ("digits", "brightness")

# A floor because a zero or negative interval is a busy loop against ADB, and
# a ceiling because an hour between scans is indistinguishable from a hang.
MIN_INTERVAL = 0.1
MAX_INTERVAL = 3600.0


class ControlError(ValueError):
    """A patch the caller may not apply. `field` names the offending key."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _default_actions() -> set[str]:
    return {action.name for action in config.ACTIONS}


@dataclass
class Controls:
    """The live knobs. Construct from CLI args; mutate through apply()."""

    paused: bool = False
    interval: float = config.SCAN_INTERVAL_SECONDS
    auto_navigate: bool = False
    strategy: str = "digits"
    enabled_actions: set[str] = field(default_factory=_default_actions)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        """A JSON-safe, detached copy. Safe to hand to a request handler."""
        with self._lock:
            return {
                "paused": self.paused,
                "interval": self.interval,
                "auto_navigate": self.auto_navigate,
                "strategy": self.strategy,
                # Sorted so the JSON is stable and two snapshots compare equal
                # when nothing changed; a set's iteration order is not.
                "enabled_actions": sorted(self.enabled_actions),
            }

    def apply(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        """Validate the whole patch, then commit it. Returns what changed.

        All-or-nothing: a patch whose third field is invalid must not leave the
        first two applied. Validation therefore happens entirely before the
        lock is taken to write anything.
        """
        staged: dict[str, Any] = {}

        if "paused" in patch:
            staged["paused"] = bool(patch["paused"])

        if "auto_navigate" in patch:
            staged["auto_navigate"] = bool(patch["auto_navigate"])

        if "interval" in patch:
            try:
                interval = float(patch["interval"])
            except (TypeError, ValueError):
                raise ControlError("interval", "interval must be a number") from None
            if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
                raise ControlError(
                    "interval", f"interval must be between {MIN_INTERVAL} and {MAX_INTERVAL}"
                )
            staged["interval"] = interval

        if "strategy" in patch:
            strategy = patch["strategy"]
            if strategy not in STRATEGIES:
                raise ControlError("strategy", f"strategy must be one of {STRATEGIES}")
            staged["strategy"] = strategy

        if "enabled_actions" in patch:
            actions = patch["enabled_actions"]
            if not isinstance(actions, (list, tuple, set)) or not all(
                isinstance(name, str) for name in actions
            ):
                raise ControlError("enabled_actions", "enabled_actions must be a list of names")
            staged["enabled_actions"] = set(actions)

        changed: dict[str, Any] = {}
        with self._lock:
            for key, value in staged.items():
                if getattr(self, key) == value:
                    continue
                setattr(self, key, value)
                # Report the JSON shape, not the internal one - this dict goes
                # straight into a ControlChanged event and out to the browser.
                changed[key] = sorted(value) if isinstance(value, set) else value
        return changed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_control.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add control.py tests/test_control.py
git commit -m "feat: add the live control settings"
```

---

### Task 2: The ControlChanged event

**Files:**
- Modify: `events.py`
- Test: `tests/test_store_sink.py` (append one test)

**Interfaces:**
- Produces: `events.ControlChanged(changed: dict[str, Any], source: str = "web")`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_store_sink.py`. Match the file's existing fixtures — read the top of it first and reuse whatever it already uses to build a sink against an in-memory or tmp database:

```python
def test_control_changes_are_persisted(tmp_path) -> None:
    """A setting change must be as reconstructable as every other change.

    "Why did it stop tapping at 3am" is exactly the question the event log
    exists to answer, so the control surface cannot be the one thing that
    mutates the bot without leaving a row behind.
    """
    import db
    import events
    from sinks.store import StoreSink

    path = tmp_path / "events.db"
    sink = StoreSink(path)
    sink.start()
    try:
        bus = events.EventBus()
        bus.subscribe(sink)
        bus.publish(events.ControlChanged(changed={"paused": True}, source="web"))
    finally:
        sink.stop()

    with db.reader(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute("SELECT type, detail FROM events").fetchall()
        ]
    assert rows and rows[0]["type"] == "ControlChanged"
    # The events table has no column for `changed`; the JSON detail blob is
    # what absorbs a new event type with no schema migration.
    assert "paused" in rows[0]["detail"]
```

If `StoreSink.stop()` is not the actual teardown method, use whatever `tests/test_store_sink.py` already calls.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_store_sink.py -k control -v`
Expected: FAIL with `AttributeError: module 'events' has no attribute 'ControlChanged'`.

- [ ] **Step 3: Add the event**

In `events.py`, after `BotError`:

```python
@dataclass(frozen=True, kw_only=True)
class ControlChanged(Event):
    """A live setting changed. `changed` holds only the fields that moved.

    Persisted through the events table's JSON `detail` blob, so it needs no
    schema migration - there is no `changed` column and there does not need
    to be one.
    """

    changed: dict[str, Any]
    source: str = "web"
```

`Any` is already imported at the top of `events.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store_sink.py -v && uv run pytest -q`
Expected: all green. If the store sink maps event fields to columns by name, confirm it tolerates an event with no `screen`/`action`/`score` — every other event type already exercises that path, so it should.

- [ ] **Step 5: Commit**

```bash
git add events.py tests/test_store_sink.py
git commit -m "feat: record control changes as events"
```

---

### Task 3: The loop reads Controls

**Files:**
- Modify: `tower_bot.py` — `TowerBot.__init__`, `run_once`, `run_forever`, and the `TowerBot(...)` construction in `main()`
- Test: `tests/test_gating.py` (append)

**Interfaces:**
- Consumes: `Controls` from Task 1.
- Produces: `TowerBot(..., controls: Controls, checks: dict[str, AffordabilityCheck | None])`. The `auto_navigate` parameter is **removed**.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gating.py`, reusing whatever fixture that file already uses to build a bot against a fake device (read the top of the file first):

```python
def test_paused_keeps_scanning_but_never_taps(bot_in_run) -> None:
    """Pause must not blind the dashboard.

    A paused bot that stopped reporting would blank the dashboard at the exact
    moment you paused it to look at something, and would lose the screen
    tracking that makes resuming safe. So it still captures, classifies and
    publishes ScanCompleted - it just does not act.
    """
    bot, seen = bot_in_run
    bot.controls.apply({"paused": True})

    bot.run_once()

    kinds = [event.type for event in seen]
    assert "ScanCompleted" in kinds
    assert "Tapped" not in kinds
    assert any(
        event.type == "Skipped" and event.reason == "paused" for event in seen
    )


def test_resuming_taps_again(bot_in_run) -> None:
    bot, seen = bot_in_run
    bot.controls.apply({"paused": True})
    bot.run_once()
    seen.clear()
    bot.controls.apply({"paused": False})

    bot.run_once()

    assert any(event.type == "Tapped" for event in seen)


def test_disabled_actions_are_not_tapped(bot_in_run) -> None:
    bot, seen = bot_in_run
    bot.controls.apply({"enabled_actions": []})

    bot.run_once()

    assert not [event for event in seen if event.type == "Tapped"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_gating.py -v`
Expected: FAIL — `TowerBot` has no `controls` attribute.

- [ ] **Step 3: Take `Controls` in the constructor**

In `TowerBot.__init__`, **remove** the `auto_navigate: bool = False` parameter and the `self.auto_navigate = auto_navigate` line. Add:

```python
        controls: Controls | None = None,
        checks: dict[str, AffordabilityCheck | None] | None = None,
```

and in the body, replacing `self.auto_navigate = auto_navigate`:

```python
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
```

Add the import at the top of `tower_bot.py`:

```python
from control import Controls
```

- [ ] **Step 4: Read the snapshot once per pass**

In `run_once`, immediately after `started = time.monotonic()` and **before** `self.refresh_screen()`:

```python
        # Exactly one snapshot for the whole pass. Re-reading mid-scan would
        # let a setting change underneath a half-finished scan - the wallet
        # read with one strategy and the price gate applied with another.
        settings = self.controls.snapshot()
        chosen = self.checks.get(settings["strategy"])
        if chosen is not None:
            self.affordability = chosen
```

Replace the action loop's gate:

```python
        clicked = False
        if state is screens.ScreenState.IN_RUN:
            for action in config.ACTIONS:
                if self.find_and_click_image(action):
                    clicked = True
```

with:

```python
        clicked = False
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
                if self.find_and_click_image(action):
                    clicked = True
```

Replace the auto-navigate line:

```python
        if self.auto_navigate and not self.run_cap_reached(max_runs):
```

with:

```python
        if (
            settings["auto_navigate"]
            and not settings["paused"]
            and not self.run_cap_reached(max_runs)
        ):
```

- [ ] **Step 5: Let the interval change while running**

In `run_forever`, replace `time.sleep(interval)` with:

```python
            # Re-read rather than using the argument: an interval changed from
            # the dashboard takes effect on the next sleep, not on restart.
            time.sleep(self.controls.interval)
```

and change the signature's `interval` parameter to seed the controls instead of being used directly. At the top of `run_forever`, before the loop:

```python
        if interval != config.SCAN_INTERVAL_SECONDS:
            # An explicit argument still wins at startup - serve_web() passes
            # one - but the dashboard owns it from then on.
            self.controls.apply({"interval": interval})
        logger.info("Bot started - scanning every %.1fs. Ctrl+C to stop.", self.controls.interval)
```

removing the original `logger.info` line.

- [ ] **Step 6: Update every caller**

Find the tests that pass the removed parameter:

```bash
grep -rn "auto_navigate" tests/ tower_bot.py
```

Replace each `TowerBot(..., auto_navigate=True)` with `TowerBot(..., controls=Controls(auto_navigate=True))`, importing `Controls` in those test files. In `main()`, replace `auto_navigate=args.auto_navigate` in the `TowerBot(...)` call with the `controls=` and `checks=` arguments Task 5 defines — for now, temporarily:

```python
            controls=Controls(auto_navigate=args.auto_navigate, strategy=args.affordability),
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add tower_bot.py tests/
git commit -m "feat: drive the scan loop from live controls"
```

---

### Task 4: The control endpoints

**Files:**
- Modify: `web/app.py`
- Test: `tests/test_control_api.py`

**Interfaces:**
- Consumes: `Controls` from Task 1, `ControlChanged` from Task 2.
- Produces: `create_app(..., controls: Controls | None = None, checks: Mapping[str, object] | None = None)` serving `GET /api/control`, `PATCH /api/control`, `POST /api/control/stop`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_control_api.py`:

```python
"""The three routes that write - to memory, never to the database."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

import config
from control import Controls
from events import EventBus
from sinks.sse import SseSink
from sinks.state import BotState
from web.app import create_app


@pytest.fixture
def wired() -> tuple[TestClient, Controls, list, threading.Event]:
    seen: list = []

    class Recorder:
        def offer(self, event) -> bool:
            seen.append(event)
            return True

    bus = EventBus()
    bus.subscribe(Recorder())
    controls = Controls(paused=False, interval=2.0, strategy="brightness")
    stop = threading.Event()
    app = create_app(
        state=BotState(), sse=SseSink(), bus=bus, db_path=None,
        unknown_dir=config.UNKNOWN_DIR, stop=stop,
        controls=controls, checks={"brightness": object(), "digits": None},
    )
    return TestClient(app), controls, seen, stop


def test_get_returns_the_current_settings_and_the_action_names(wired) -> None:
    client, _, _, _ = wired
    body = client.get("/api/control").json()
    assert body["paused"] is False
    assert body["strategy"] == "brightness"
    assert body["actions"] == [action.name for action in config.ACTIONS]


def test_patch_applies_and_echoes_the_full_state(wired) -> None:
    client, controls, _, _ = wired
    body = client.patch("/api/control", json={"paused": True}).json()
    assert body["paused"] is True
    # The full state, not just the delta: a client must never have to guess
    # what was accepted.
    assert "interval" in body and "enabled_actions" in body
    assert controls.snapshot()["paused"] is True


def test_patch_publishes_a_control_changed_event(wired) -> None:
    client, _, seen, _ = wired
    client.patch("/api/control", json={"interval": 5.0})
    published = [event for event in seen if event.type == "ControlChanged"]
    assert published and published[0].changed == {"interval": 5.0}


def test_a_no_op_patch_publishes_nothing(wired) -> None:
    client, _, seen, _ = wired
    client.patch("/api/control", json={"paused": False})
    assert not [event for event in seen if event.type == "ControlChanged"]


def test_an_invalid_patch_is_422_and_changes_nothing(wired) -> None:
    client, controls, seen, _ = wired
    response = client.patch("/api/control", json={"interval": -1})
    assert response.status_code == 422
    assert controls.snapshot()["interval"] == 2.0
    assert not [event for event in seen if event.type == "ControlChanged"]


def test_switching_to_an_unavailable_strategy_is_refused(wired) -> None:
    """Silently downgrading would misrepresent what the bot is doing.

    build_affordability() degrades digits -> brightness on its own, which is
    right for a startup flag and wrong for a live setting: you would ask for
    digits, get brightness, and never be told.
    """
    client, controls, _, _ = wired
    response = client.patch("/api/control", json={"strategy": "digits"})
    assert response.status_code == 422
    assert "atlas" in response.json()["detail"].lower()
    assert controls.snapshot()["strategy"] == "brightness"


def test_stop_sets_the_shutdown_flag(wired) -> None:
    client, _, _, stop = wired
    assert client.post("/api/control/stop").status_code == 200
    assert stop.is_set()


def test_control_routes_are_absent_when_no_controls_were_wired(wired) -> None:
    """--once and the tests that predate this build an app with no controls."""
    app = create_app(
        state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None,
        unknown_dir=config.UNKNOWN_DIR, stop=threading.Event(),
    )
    assert TestClient(app).get("/api/control").status_code == 404
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_control_api.py -v`
Expected: FAIL — `create_app()` got an unexpected keyword argument `controls`.

- [ ] **Step 3: Add the routes**

In `web/app.py`, extend the imports:

```python
from typing import Any, Mapping
from pydantic import BaseModel
import config
import events
from control import ControlError, Controls
```

Add the request model above `create_app`:

```python
class ControlPatch(BaseModel):
    """A partial update. Every field optional; absent means "leave it alone".

    Deliberately loose on types beyond the obvious - Controls.apply() is the
    single validator, so the rules live in one place rather than being spelled
    out here and there and drifting apart.
    """

    paused: bool | None = None
    interval: float | None = None
    auto_navigate: bool | None = None
    strategy: str | None = None
    enabled_actions: list[str] | None = None
```

Extend `create_app`'s signature with:

```python
    controls: Controls | None = None,
    checks: Mapping[str, Any] | None = None,
```

and add the routes inside `create_app`, **before** the static mount:

```python
    if controls is not None:
        available = dict(checks or {})

        @app.get("/api/control")
        def read_control() -> dict:
            payload = controls.snapshot()
            # The browser needs the full action list to render checkboxes for
            # the ones currently switched off, which the snapshot omits.
            payload["actions"] = [action.name for action in config.ACTIONS]
            payload["strategies_available"] = sorted(
                name for name, check in available.items() if check is not None
            )
            return payload

        @app.patch("/api/control")
        def patch_control(patch: ControlPatch) -> dict:
            requested = patch.model_dump(exclude_none=True)

            # Refuse before applying, not after: build_affordability() falls
            # back to brightness on its own, so accepting this and letting the
            # loop pick would leave the browser showing "digits" while the bot
            # used brightness.
            strategy = requested.get("strategy")
            if strategy is not None and available.get(strategy) is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"strategy {strategy!r} is unavailable - no glyph atlas is built",
                )

            try:
                changed = controls.apply(requested)
            except ControlError as exc:
                raise HTTPException(status_code=422, detail=f"{exc.field}: {exc}") from exc

            if changed:
                # Only when something actually moved. A no-op patch is not a
                # state change and must not fill the log with noise.
                bus.publish(events.ControlChanged(changed=changed, source="web"))

            payload = controls.snapshot()
            payload["actions"] = [action.name for action in config.ACTIONS]
            payload["strategies_available"] = sorted(
                name for name, check in available.items() if check is not None
            )
            return payload

        @app.post("/api/control/stop")
        def stop_bot() -> dict:
            # The same path --max-runs already takes: set the shared flag, let
            # serve_web()'s finally bring the loop and the server down together.
            bus.publish(events.ControlChanged(changed={"stopping": True}, source="web"))
            stop.set()
            return {"stopping": True}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_control_api.py -v && uv run pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_control_api.py
git commit -m "feat: serve the control plane over HTTP"
```

---

### Task 5: Wire it into the bot

**Files:**
- Modify: `tower_bot.py` — `main()`, `serve_web()`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: `--web` serves working control routes; `POST /api/control/stop` ends the process.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_controls_start_from_the_command_line() -> None:
    """The flags you launched with must not be silently overridden.

    Controls' dataclass defaults are a fallback; main() seeds them from argv.
    """
    from control import Controls
    from tower_bot import parse_args

    args = parse_args(["--auto-navigate", "--affordability", "brightness"])
    controls = Controls(auto_navigate=args.auto_navigate, strategy=args.affordability)
    assert controls.snapshot()["auto_navigate"] is True
    assert controls.snapshot()["strategy"] == "brightness"
```

- [ ] **Step 2: Run it to verify it fails or passes**

Run: `uv run pytest tests/test_cli.py -k controls -v`
Expected: PASS if Tasks 1 and 3 are complete. This one is a guard against a later regression rather than a driver — if it fails, `Controls` is not taking the arguments Task 1 defined.

- [ ] **Step 3: Build the checks and the controls in `main()`**

In `main()`, replace:

```python
        bot = TowerBot(
            device=device,
            templates=vision.TemplateCache(config.TEMPLATE_DIR),
            bus=bus,
            affordability_check=build_affordability(args.affordability),
            auto_navigate=args.auto_navigate,
            first_run_id=last_run + 1,
        )
```

with:

```python
        # Both strategies built once, at startup. `digits` degrades to
        # brightness when no atlas exists, and the identity check below is how
        # we notice - so the dashboard can refuse a switch to digits with a
        # reason rather than quietly handing back brightness.
        brightness = BrightnessAffordability()
        digits_check = build_affordability("digits")
        checks: dict[str, AffordabilityCheck | None] = {
            "brightness": brightness,
            "digits": digits_check if isinstance(digits_check, DigitAffordability) else None,
        }
        controls = Controls(
            auto_navigate=args.auto_navigate,
            strategy=args.affordability if checks.get(args.affordability) else "brightness",
        )
        bot = TowerBot(
            device=device,
            templates=vision.TemplateCache(config.TEMPLATE_DIR),
            bus=bus,
            affordability_check=checks[controls.strategy] or brightness,
            controls=controls,
            checks=checks,
            first_run_id=last_run + 1,
        )
```

Confirm `BrightnessAffordability`, `DigitAffordability` and `AffordabilityCheck` are all in the existing `from affordability import (...)` block at the top of `tower_bot.py`; add whichever is missing.

- [ ] **Step 4: Pass the controls into the app**

Find where `main()` builds the FastAPI app for `--web` and add the two new arguments:

```python
            controls=controls,
            checks=checks,
```

- [ ] **Step 5: Say that the surface is no longer read-only**

Spec §9's first risk row. Two places assert the dashboard is safe on loopback because it only reads; after this plan that is no longer true, and both need correcting.

In `config.py`, extend the comment beside `WEB_HOST` — keep whatever is already there and add:

```python
# Since the control plane landed this server is no longer read-only: anything
# that can reach it can pause the bot, change what it buys, and stop the
# process. Loopback is doing real work here, not just avoiding an open port.
```

In `web/app.py`, update the module docstring's existing warning:

```
Binds 127.0.0.1. No auth, /api/unknown serves screenshots of a live session,
and /api/control lets a caller pause, reconfigure or stop the bot - see the
warning beside config.WEB_HOST before changing the bind address.
```

Check that `tower_bot.py`'s non-loopback `--web-host` warning (the "the dashboard's live ..." message) still reads correctly now that the surface writes; if it describes the risk as exposure of screenshots only, extend it to mention control.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green.

- [ ] **Step 7: Verify by hand**

```bash
uv run tower_bot.py --web --no-store
```

In another terminal:

```bash
curl -s http://127.0.0.1:8765/api/control
curl -s -X PATCH http://127.0.0.1:8765/api/control -H 'content-type: application/json' -d '{"paused": true}'
curl -s -X PATCH http://127.0.0.1:8765/api/control -H 'content-type: application/json' -d '{"interval": -1}'   # expect 422
curl -s -X POST http://127.0.0.1:8765/api/control/stop
```

The bot must stop scanning after the pause, reject the bad interval, keep serving throughout, and exit cleanly on stop.

- [ ] **Step 8: Commit**

```bash
git add tower_bot.py tests/test_cli.py
git commit -m "feat: wire the control plane into the running bot"
```

---

### Task 6: The control page

**Files:**
- Modify: `web/ui/lib/types.ts`, `web/ui/lib/api.ts`
- Create: `web/ui/app/control/page.tsx`

**Interfaces:**
- Consumes: the routes from Task 4, and Plan A's `getJson` helper.
- Produces: `/control/` in the dashboard.

- [ ] **Step 1: Add the types and the client calls**

Append to `web/ui/lib/types.ts`:

```ts
export interface ControlPayload {
  paused: boolean;
  interval: number;
  auto_navigate: boolean;
  strategy: string;
  enabled_actions: string[];
  actions: string[];
  strategies_available: string[];
}
```

Add `ControlPayload` to the existing `import type { ... } from "./types";` line at the top of `web/ui/lib/api.ts`, then append:

```ts
export const fetchControl = () => getJson<ControlPayload>("/api/control");

/** Returns the full new state, or throws with the server's reason. */
export async function patchControl(patch: Partial<ControlPayload>): Promise<ControlPayload> {
  const response = await fetch("/api/control", {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail ?? `PATCH /api/control -> ${response.status}`);
  return body as ControlPayload;
}

export async function stopBot(): Promise<void> {
  await fetch("/api/control/stop", { method: "POST" });
}
```

- [ ] **Step 2: Write the page**

Create `web/ui/app/control/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { fetchControl, patchControl, stopBot } from "@/lib/api";
import type { ControlPayload } from "@/lib/types";

export default function ControlPage() {
  const [control, setControl] = useState<ControlPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchControl().then(setControl).catch((e) => setError(String(e)));
  }, []);

  // Every write goes through here so the page always renders what the server
  // actually accepted, never what we optimistically hoped it would.
  async function send(patch: Partial<ControlPayload>) {
    setError(null);
    try {
      setControl(await patchControl(patch));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (!control) {
    return <p className="text-sm text-muted-foreground">{error ?? "Loading…"}</p>;
  }

  const toggleAction = (name: string) =>
    send({
      enabled_actions: control.enabled_actions.includes(name)
        ? control.enabled_actions.filter((a) => a !== name)
        : [...control.enabled_actions, name],
    });

  return (
    <div className="flex max-w-xl flex-col gap-4">
      {error ? <p className="rounded-md border border-red-500 p-2 text-sm text-red-500">{error}</p> : null}

      <section className="flex gap-2 rounded-lg border p-3">
        <button onClick={() => send({ paused: !control.paused })}
                className="rounded-md border px-3 py-1.5 text-sm">
          {control.paused ? "Resume" : "Pause"}
        </button>
        <button
          onClick={() => { if (confirm("Stop the bot and the dashboard?")) void stopBot(); }}
          className="rounded-md border border-red-500 px-3 py-1.5 text-sm text-red-500"
        >
          Stop
        </button>
        <span className="self-center text-sm text-muted-foreground">
          {control.paused ? "paused — scanning, not tapping" : "running"}
        </span>
      </section>

      <section className="flex flex-col gap-3 rounded-lg border p-3">
        <label className="flex items-center justify-between text-sm">
          Scan interval (s)
          <input
            type="number" min={0.1} max={3600} step={0.1} defaultValue={control.interval}
            onBlur={(e) => send({ interval: Number(e.target.value) })}
            className="w-24 rounded-md border bg-transparent px-2 py-1 text-right"
          />
        </label>

        <label className="flex items-center justify-between text-sm">
          Auto-navigate
          <input type="checkbox" checked={control.auto_navigate}
                 onChange={(e) => send({ auto_navigate: e.target.checked })} />
        </label>

        <div className="text-sm">
          <div className="mb-1">Affordability</div>
          {["digits", "brightness"].map((name) => {
            const usable = control.strategies_available.includes(name);
            return (
              <label key={name} className={`mr-4 ${usable ? "" : "text-muted-foreground"}`}>
                <input type="radio" name="strategy" checked={control.strategy === name}
                       disabled={!usable} onChange={() => send({ strategy: name })} />{" "}
                {name}{usable ? "" : " (no atlas)"}
              </label>
            );
          })}
        </div>

        <div className="text-sm">
          <div className="mb-1">Actions</div>
          {control.actions.map((name) => (
            <label key={name} className="mr-4 inline-block">
              <input type="checkbox" checked={control.enabled_actions.includes(name)}
                     onChange={() => toggleAction(name)} />{" "}
              {name}
            </label>
          ))}
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 3: Build, test, and check by hand**

```bash
cd web/ui && npm test && npm run build && cd ../.. && uv run pytest -q
uv run tower_bot.py --web --no-store
```

Open `http://127.0.0.1:8765/control/`. Pause and watch the feed keep scanning while `Tapped` stops. Change the interval and watch the scan cadence change. Untick an action. Try switching to digits with no atlas and confirm the radio is disabled rather than silently lying.

- [ ] **Step 4: Commit**

```bash
git add web/ui web/static
git commit -m "feat: add the dashboard's control page"
```

---

## Done when

- `uv run pytest -q` and `npm test` both pass.
- Pausing from the browser stops taps and auto-navigation while `ScanCompleted` keeps arriving in the feed.
- Changing the interval changes the scan cadence without a restart.
- Unticking an action stops that upgrade being bought and nothing else.
- Switching to a strategy with no atlas returns 422 and leaves the setting where it was.
- Stop ends the loop, the server and the process, with a dashboard tab still open.
- Every accepted change appears in the live feed and in the `events` table as `ControlChanged`.
- `web/static/` is rebuilt and committed; the freshness test passes.

## Follow-on plans

- **Plan C** — `docs/superpowers/plans/2026-09-01-dashboard-c-views.md`: live device screen, run drill-down, stats, errors.
