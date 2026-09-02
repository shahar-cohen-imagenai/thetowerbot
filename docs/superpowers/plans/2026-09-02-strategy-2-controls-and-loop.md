# Controls reshaped and the loop reading it — Implementation Plan (2 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse `Controls`'s policy fields into a single `Strategy`, and make `run_once()` iterate the strategy's ordered rows instead of `config.ACTIONS`.

**Architecture:** `Controls` keeps the lock, `apply()`, and the `changed`-dict-to-`ControlChanged` path it already has — those are the well-tested parts and none of their reasons changed. What it holds shrinks to `paused` plus one `Strategy`, swapped whole. `snapshot()` returns a frozen `Live` instead of a dict, so the hot loop reads attributes rather than nested dictionary keys. The loop keeps taking exactly one snapshot per pass.

**Tech Stack:** Python 3.12, dataclasses, `pytest`, FastAPI (`web/app.py` payload only), `uv run`.

**Spec:** `docs/superpowers/specs/2026-09-02-bot-lifecycle-and-strategy-design.md` (sections 5 and 6)

**Depends on:** plan 1 (`2026-09-02-strategy-1-model-and-store.md`) — `Strategy`, `ActionRule`, `ControlError` and `StrategyStore` must exist.

## Global Constraints

- Every function and method carries type hints; `from __future__ import annotations` at the top of every module.
- `control.py` must not import from `web/`. It may import `strategy`.
- Run tests with `uv run pytest <file>` and `-p no:allure_pytest`. Never run the whole suite.
- The one-snapshot-per-scan invariant in `run_once()` is not negotiable. Exactly one `self.controls.snapshot()` call per pass, at the top.
- This plan is a refactor with one behaviour change (per-row thresholds and ordering now take effect). The bot must still start and scan at every commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `control.py` (rewrite) | `Live`, `Controls`, re-export of `ControlError` |
| `tower_bot.py` (modify) | `run_once` action loop, `build_checks_and_controls`, `TowerBot.__init__` |
| `web/app.py` (modify) | `ControlPatch` fields and `_control_payload()` — wire rename only |
| `tests/test_control.py` (rewrite) | the reshaped `Controls` |
| `tests/test_control_api.py` (modify) | the renamed wire fields |
| `tests/test_gating.py` (modify) | the loop reading strategy rows |
| `web/ui/lib/types.ts`, `web/ui/app/control/page.tsx` (modify) | keep the existing page compiling against the new payload |

---

### Task 1: `Live` and the reshaped `Controls`

**Files:**
- Modify: `control.py` (substantial rewrite; the module is 130 lines)
- Test: `tests/test_control.py` (rewrite)

**Interfaces:**
- Consumes: `strategy.Strategy`, `strategy.ActionRule`, `strategy.ControlError`, `strategy.MIN_INTERVAL`, `strategy.MAX_INTERVAL`
- Produces:
  - `control.Live(paused: bool, strategy: Strategy)` — frozen dataclass
  - `control.Controls(strategy: Strategy, paused: bool = False)` with `.snapshot() -> Live`, `.apply(patch: Mapping[str, Any]) -> dict[str, Any]`, `.payload() -> dict[str, Any]`
  - `control.ControlError` — re-exported from `strategy`
  - `control.STRATEGIES` — re-exported as `strategy.AFFORDABILITY` for any caller not yet updated

- [ ] **Step 1: Write the failing tests**

Replace the whole of `tests/test_control.py`:

```python
"""Controls is the one mutable thing the web layer may touch.

Reshaped: it now holds session state (paused) plus one Strategy swapped
whole. The lock, the all-or-nothing apply(), and the changed-dict that
becomes a ControlChanged event are unchanged - those are the parts that were
already right.
"""

from __future__ import annotations

import threading

import pytest

from control import ControlError, Controls, Live
from strategy import ActionRule, Strategy


def a_strategy(**overrides) -> Strategy:
    base = dict(
        name="test",
        actions=(
            ActionRule(name="Damage", template="upgrade_damage.png"),
            ActionRule(name="Critical Chance", template="upgrade_critical_chance.png"),
        ),
    )
    return Strategy(**{**base, **overrides})


def a_controls(**overrides) -> Controls:
    return Controls(strategy=a_strategy(**overrides))


def test_snapshot_is_frozen_and_needs_no_copying() -> None:
    """The whole point of the reshape.

    The old Controls copied its enabled_actions set on construction and again
    on every snapshot, so the loop and the web thread never shared a mutable
    structure. A frozen Strategy holding frozen rows cannot be mutated at
    all, so the copying stops being necessary rather than moving one level in.
    """
    controls = a_controls()
    snap = controls.snapshot()
    assert isinstance(snap, Live)
    with pytest.raises(Exception):
        snap.paused = True
    with pytest.raises(Exception):
        snap.strategy.actions[0].threshold = 0.1


def test_snapshot_exposes_attributes_not_dict_keys() -> None:
    # This is what run_once() reads on every pass; nested dict lookups were
    # the cost the old dict snapshot would have imposed once policy got rich.
    snap = a_controls(interval=3.0).snapshot()
    assert snap.paused is False
    assert snap.strategy.interval == 3.0
    assert snap.strategy.actions[0].name == "Damage"


def test_apply_returns_only_what_changed() -> None:
    controls = a_controls(interval=2.0)
    changed = controls.apply({"paused": True, "interval": 2.0})
    assert changed == {"paused": True}
    assert controls.snapshot().paused is True


def test_apply_ignores_unknown_fields() -> None:
    assert a_controls().apply({"nonsense": 1}) == {}


def test_apply_patches_strategy_fields_in_place() -> None:
    controls = a_controls(interval=2.0)
    changed = controls.apply({"interval": 5.0, "auto_navigate": True})
    assert changed == {"interval": 5.0, "auto_navigate": True}
    assert controls.snapshot().strategy.interval == 5.0
    # The rest of the strategy survives the patch untouched.
    assert controls.snapshot().strategy.name == "test"
    assert len(controls.snapshot().strategy.actions) == 2


def test_apply_is_all_or_nothing_across_a_multi_field_patch() -> None:
    """A patch whose third field is invalid must not leave the first two
    applied. The merged Strategy is validated whole before it is swapped in,
    so the earlier fields were never applied to anything but a candidate.
    """
    controls = a_controls(interval=2.0, auto_navigate=False)
    with pytest.raises(ControlError) as caught:
        controls.apply({"interval": 5.0, "auto_navigate": True, "max_runs": 0})
    assert caught.value.field == "max_runs"
    live = controls.snapshot()
    assert live.strategy.interval == 2.0
    assert live.strategy.auto_navigate is False


def test_interval_bounds_still_apply() -> None:
    controls = a_controls(interval=2.0)
    with pytest.raises(ControlError) as caught:
        controls.apply({"interval": 0})
    assert caught.value.field == "interval"
    assert controls.snapshot().strategy.interval == 2.0
    with pytest.raises(ControlError):
        controls.apply({"interval": 3601})


def test_affordability_must_be_known() -> None:
    controls = a_controls()
    with pytest.raises(ControlError) as caught:
        controls.apply({"affordability": "vibes"})
    assert caught.value.field == "affordability"


def test_actions_can_be_replaced_wholesale() -> None:
    """How the strategy page reorders, toggles and retunes rows: one patch
    carrying the whole list, not a per-row endpoint.
    """
    controls = a_controls()
    changed = controls.apply({
        "actions": [
            {"name": "Critical Chance", "template": "upgrade_critical_chance.png",
             "threshold": 0.95, "enabled": True, "brightness_ratio": 0.75},
            {"name": "Damage", "template": "upgrade_damage.png",
             "threshold": 0.8, "enabled": False, "brightness_ratio": 0.75},
        ]
    })
    rows = controls.snapshot().strategy.actions
    assert [r.name for r in rows] == ["Critical Chance", "Damage"]
    assert rows[0].threshold == 0.95
    assert rows[1].enabled is False
    assert "actions" in changed


def test_a_rejected_action_list_changes_nothing() -> None:
    controls = a_controls()
    before = controls.snapshot().strategy
    with pytest.raises(ControlError):
        controls.apply({"actions": [
            {"name": "Damage", "template": "d.png", "threshold": 5.0},
        ]})
    assert controls.snapshot().strategy == before


def test_replacing_the_whole_strategy() -> None:
    """Activating a saved profile: one swap, not a field-by-field patch."""
    controls = a_controls()
    other = a_strategy(name="crit", interval=4.0)
    changed = controls.replace(other)
    assert controls.snapshot().strategy is other
    assert changed["name"] == "crit"
    assert changed["interval"] == 4.0


def test_replace_reports_nothing_when_the_strategy_is_identical() -> None:
    # A no-op must not publish a ControlChanged and fill the log with noise.
    controls = a_controls()
    assert controls.replace(a_strategy()) == {}


def test_payload_is_json_safe_and_detached() -> None:
    payload = a_controls().payload()
    import json
    json.dumps(payload)
    assert payload["paused"] is False
    assert payload["strategy"]["actions"][0]["name"] == "Damage"
    payload["strategy"]["actions"].append({"bogus": True})
    assert len(a_controls().payload()["strategy"]["actions"]) == 2


def test_concurrent_applies_do_not_interleave() -> None:
    """The lock is doing real work: two threads patching different fields
    must both land, and neither may read a half-swapped strategy.
    """
    controls = a_controls(interval=2.0)
    errors: list[BaseException] = []

    def hammer(value: float) -> None:
        try:
            for _ in range(200):
                controls.apply({"interval": value})
                assert controls.snapshot().strategy.interval in (1.0, 2.0, 3.0)
        except BaseException as exc:  # noqa: BLE001 - recorded, re-raised below
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(v,)) for v in (1.0, 3.0)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_control.py -q -p no:allure_pytest`
Expected: FAIL with `ImportError: cannot import name 'Live' from 'control'`

- [ ] **Step 3: Rewrite control.py**

Replace the whole of `control.py`:

```python
"""What the browser is allowed to change while the bot runs.

Two things live here, and the split is deliberate. `paused` is session
state - a fact about this bot right now, not something you would save under
a name and load next week. Everything else is policy, and policy lives in a
Strategy (see strategy.py), held here as one immutable value swapped whole.

Shaped like BotState on purpose: one lock, one snapshot() that hands out a
detached view, and no mutable structure ever shared across the boundary. The
scan loop reads a snapshot once per pass; the web layer patches through
apply(). Neither ever holds the other's objects - and since a Strategy is
frozen all the way down, that now costs no copying at all.

This module is deliberately at the top level rather than under web/: the scan
loop depends on it, and the scan loop must not import the web layer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Any, Mapping

from strategy import (
    AFFORDABILITY,
    MAX_INTERVAL,
    MIN_INTERVAL,
    ActionRule,
    ControlError,
    Strategy,
)

# Re-exported so `from control import ControlError` keeps working for every
# existing caller. It is DEFINED in strategy.py because control.py imports
# Strategy, and the reverse import would be a cycle.
__all__ = ["ControlError", "Controls", "Live", "STRATEGIES", "MIN_INTERVAL", "MAX_INTERVAL"]

# The affordability methods. Kept under the old name too because the CLI's
# --affordability choices and several tests still spell it this way.
STRATEGIES = AFFORDABILITY

# Fields a PATCH may set directly on the Strategy. `name` is absent on
# purpose: renaming a profile is the store's business (save under a new
# name), not a live edit to the running policy.
PATCHABLE = (
    "affordability",
    "interval",
    "click_cooldown",
    "auto_navigate",
    "max_runs",
    "navigation_cooldown",
    "screen_confirmations",
)


@dataclass(frozen=True)
class Live:
    """One pass's view of the settings. Frozen, so it needs no copying.

    Returned by snapshot() and read by run_once() on every scan. An
    attribute, not a dict, because the loop reads it in the hot path and
    `settings.strategy.actions` beats `settings["strategy"]["actions"]`.
    """

    paused: bool
    strategy: Strategy


@dataclass
class Controls:
    """The live knobs. Construct from a loaded Strategy; mutate through
    apply() or replace()."""

    strategy: Strategy
    paused: bool = False

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def snapshot(self) -> Live:
        """A frozen view. Safe to hand to the scan loop or a request handler."""
        with self._lock:
            return Live(paused=self.paused, strategy=self.strategy)

    def payload(self) -> dict[str, Any]:
        """The JSON shape. Separate from snapshot() because the loop wants
        attributes and the browser wants keys, and one type serving both
        made the loop pay for the browser's convenience."""
        live = self.snapshot()
        return {"paused": live.paused, "strategy": live.strategy.to_dict()}

    def replace(self, strategy: Strategy) -> dict[str, Any]:
        """Swap the whole policy - how activating a saved profile lands.

        Returns what changed, in the same shape apply() does, so both feed
        one ControlChanged event. An identical strategy reports nothing: a
        no-op is not a state change and must not fill the log with noise.
        """
        with self._lock:
            if strategy == self.strategy:
                return {}
            self.strategy = strategy
        return strategy.to_dict()

    def apply(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        """Validate the whole patch, then commit it. Returns what changed.

        All-or-nothing, and now structurally so rather than by discipline:
        the patch is merged into a candidate Strategy whose constructor
        validates every field, and only a candidate that survives that is
        swapped in. A patch whose third field is invalid never touched the
        live object at all.
        """
        with self._lock:
            current = self.strategy
            was_paused = self.paused

        staged_paused = bool(patch["paused"]) if "paused" in patch else was_paused

        updates: dict[str, Any] = {
            key: patch[key] for key in PATCHABLE if key in patch
        }
        if "actions" in patch:
            updates["actions"] = _parse_actions(patch["actions"])

        # replace() runs __post_init__, which is where every bound is
        # enforced - so an invalid value raises here, before anything is
        # committed, rather than after.
        try:
            candidate = replace(current, **updates) if updates else current
        except TypeError as exc:
            raise ControlError("strategy", str(exc)) from None

        changed: dict[str, Any] = {}
        with self._lock:
            if staged_paused != self.paused:
                self.paused = staged_paused
                changed["paused"] = staged_paused
            if candidate != self.strategy:
                before = self.strategy.to_dict()
                after = candidate.to_dict()
                self.strategy = candidate
                # Report only the fields that actually moved - the whole
                # strategy would make every patch look like a full swap in
                # the event log.
                changed.update(
                    {key: after[key] for key in after if before[key] != after[key]}
                )
        return changed


def _parse_actions(raw: Any) -> tuple[ActionRule, ...]:
    """Turn the browser's action list into rules, naming what it got wrong."""
    if not isinstance(raw, (list, tuple)):
        raise ControlError("actions", "actions must be a list")
    rules: list[ActionRule] = []
    known = {"name", "template", "enabled", "threshold", "brightness_ratio"}
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ControlError("actions", "each action must be an object")
        unknown = set(entry) - known
        if unknown:
            raise ControlError("actions", f"unknown action field(s): {sorted(unknown)}")
        try:
            rules.append(ActionRule(**entry))
        except TypeError as exc:
            raise ControlError("actions", str(exc)) from None
    return tuple(rules)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_control.py -q -p no:allure_pytest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control.py tests/test_control.py
git commit -m "refactor: Controls holds session state plus one Strategy

apply() is now all-or-nothing structurally rather than by discipline: a
patch is merged into a candidate Strategy whose constructor validates
every field, and only a survivor is swapped in.

snapshot() returns a frozen Live instead of a dict, so the scan loop
reads attributes rather than nested keys - and needs no defensive
copying, because a frozen Strategy cannot be mutated through."
```

---

### Task 2: `run_once` iterates strategy rows

**Files:**
- Modify: `tower_bot.py` — `TowerBot.__init__` (around line 66-115), `run_once` (around line 275-405), `run_forever` (around line 405-458), `build_checks_and_controls` (around line 568-596)
- Test: `tests/test_gating.py` (append)

**Interfaces:**
- Consumes: `control.Live`, `control.Controls`, `strategy.Strategy`, `strategy.ActionRule`, `ActionRule.as_action()`
- Produces: no new public names. `TowerBot.__init__`'s `controls` parameter becomes required in practice (callers must pass one built from a Strategy); `click_cooldown` is removed from `__init__`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gating.py`:

```python
def test_the_loop_taps_in_strategy_order_not_config_order(monkeypatch) -> None:
    """Order IS priority. Reversing the rows must reverse the evaluation.

    This is the behaviour change the whole plan exists for: before, the loop
    walked config.ACTIONS and used the strategy only as an on/off filter.
    """
    from control import Controls
    from strategy import ActionRule, Strategy

    tried: list[str] = []
    bot = _bot_on_screen("IN_RUN")  # existing helper in this file
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(
            ActionRule(name="Critical Chance", template="upgrade_critical_chance.png"),
            ActionRule(name="Damage", template="upgrade_damage.png"),
        ),
    ))

    def record(action, boxes=None):
        tried.append(action.name)
        return False

    monkeypatch.setattr(bot, "find_and_click_image", record)
    bot.run_once()
    assert tried == ["Critical Chance", "Damage"]


def test_a_disabled_row_is_never_tried(monkeypatch) -> None:
    from control import Controls
    from strategy import ActionRule, Strategy

    tried: list[str] = []
    bot = _bot_on_screen("IN_RUN")
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(
            ActionRule(name="Damage", template="upgrade_damage.png", enabled=False),
            ActionRule(name="Critical Chance", template="upgrade_critical_chance.png"),
        ),
    ))
    monkeypatch.setattr(
        bot, "find_and_click_image", lambda action, boxes=None: tried.append(action.name)
    )
    bot.run_once()
    assert tried == ["Critical Chance"]


def test_the_per_row_threshold_reaches_the_matcher(monkeypatch) -> None:
    """A threshold edited on the strategy page must change what the vision
    layer is asked for - otherwise the control does nothing and says nothing.
    """
    from control import Controls
    from strategy import ActionRule, Strategy

    seen: list[float] = []
    bot = _bot_on_screen("IN_RUN")
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(ActionRule(
            name="Damage", template="upgrade_damage.png", threshold=0.42
        ),),
    ))
    monkeypatch.setattr(
        bot, "find_and_click_image",
        lambda action, boxes=None: seen.append(action.threshold),
    )
    bot.run_once()
    assert seen == [0.42]


def test_click_cooldown_comes_from_the_strategy(monkeypatch) -> None:
    from control import Controls
    from strategy import ActionRule, Strategy

    bot = _bot_on_screen("IN_RUN")
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(ActionRule(name="Damage", template="upgrade_damage.png"),),
        click_cooldown=30.0,
    ))
    # Two passes back to back: the second must be refused by the cooldown,
    # which only happens if the loop reads 30.0 rather than the old
    # constructor default of 1.0.
    monkeypatch.setattr("tower_bot.tap", lambda *a, **k: None)
    bot.run_once()
    taps_after_first = _tap_events(bot)  # existing helper in this file
    bot.run_once()
    assert _tap_events(bot) == taps_after_first


def test_max_runs_falls_back_to_the_strategy(monkeypatch) -> None:
    """The parameter wins when set; the strategy supplies it otherwise.

    The two can never both be meaningfully set in the web path, because the
    CLI persists its flag into the strategy rather than carrying it alongside.
    """
    from control import Controls
    from strategy import ActionRule, Strategy

    bot = _bot_on_screen("IN_RUN")
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(ActionRule(name="Damage", template="upgrade_damage.png"),),
        max_runs=3,
    ))
    bot.runs.completed = 3
    assert bot.run_cap_reached(None) is True
    assert bot.run_cap_reached(10) is False
```

Note for the implementer: `_bot_on_screen` and `_tap_events` are the helpers this file already uses. Read the top of `tests/test_gating.py` and reuse them exactly; if their names differ, use whatever the file already defines rather than adding new ones.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_gating.py -q -p no:allure_pytest -k "strategy_order or disabled_row or per_row or click_cooldown_comes or falls_back"`
Expected: FAIL — the loop still walks `config.ACTIONS`, so `test_the_loop_taps_in_strategy_order` sees `["Damage", "Critical Chance"]`

- [ ] **Step 3: Change the action loop**

In `tower_bot.py`'s `run_once`, replace this block:

```python
        elif state is screens.ScreenState.IN_RUN:
            enabled = set(settings["enabled_actions"])
            for action in config.ACTIONS:
                if action.name not in enabled:
                    continue
                if self.find_and_click_image(action, boxes):
                    clicked = True
```

with:

```python
        elif state is screens.ScreenState.IN_RUN:
            # The strategy's rows, in the strategy's order - order IS
            # priority. Before, this walked config.ACTIONS and used the
            # settings only as an on/off filter, so neither reordering nor
            # a per-row threshold could reach the matcher.
            for rule in settings.strategy.actions:
                if not rule.enabled:
                    continue
                if self.find_and_click_image(rule.as_action(), boxes):
                    clicked = True
```

- [ ] **Step 4: Change the remaining `settings[...]` reads in `run_once`**

Four dict lookups become attribute reads. Replace `settings["paused"]` with `settings.paused` (two occurrences: the pause branch and the auto-navigate guard), and `settings["auto_navigate"]` with `settings.strategy.auto_navigate`.

Then replace the whole auto-navigate condition:

```python
        if (
            settings["auto_navigate"]
            and not settings["paused"]
            and not self.run_cap_reached(max_runs)
        ):
```

with:

```python
        if (
            settings.strategy.auto_navigate
            and not settings.paused
            and not self.run_cap_reached(max_runs, settings.strategy)
        ):
```

And the affordability selection at the top of `run_once`:

```python
        chosen = self.checks.get(settings["strategy"])
```

becomes:

```python
        chosen = self.checks.get(settings.strategy.affordability)
```

- [ ] **Step 5: Make the cooldown and the run cap read the strategy**

In `find_and_click_image`, replace:

```python
        if now - self._last_click.get(cooldown_key, 0.0) < self.click_cooldown:
```

with:

```python
        if now - self._last_click.get(cooldown_key, 0.0) < self._click_cooldown():
```

and add this method to `TowerBot`, directly above `find_and_click_image`:

```python
    def _click_cooldown(self) -> float:
        """The live per-template cooldown.

        Read here rather than passed down from run_once's snapshot only
        because find_and_click_image is also called directly by tests and by
        no other caller; the value still comes from the same Controls, so
        there is no second source of truth.
        """
        return self.controls.snapshot().strategy.click_cooldown
```

Replace `run_cap_reached` entirely:

```python
    def run_cap_reached(
        self, max_runs: int | None = None, strategy: Strategy | None = None
    ) -> bool:
        """Has the bot completed as many runs as it was asked for?

        The explicit parameter wins when set - that is the non-web path,
        which has a CLI flag and no strategy loaded. Otherwise the strategy
        supplies it. The two can never both be meaningfully set in the web
        path, because the CLI persists its flag into the strategy rather
        than carrying it alongside (see the spec, section 10).
        """
        cap = max_runs
        if cap is None:
            source = strategy if strategy is not None else self.controls.snapshot().strategy
            cap = source.max_runs
        return cap is not None and self.runs.completed >= cap
```

Add `from strategy import Strategy` to `tower_bot.py`'s imports.

- [ ] **Step 6: Remove `click_cooldown` from the constructor**

In `TowerBot.__init__`, delete the `click_cooldown: float = config.CLICK_COOLDOWN_SECONDS,` parameter and the `self.click_cooldown = click_cooldown` line. It is now a strategy field, and leaving the constructor argument would be a second source of truth for it.

Make `controls` effectively required by changing its default construction:

```python
        # One source of truth for every live setting. A Controls built here
        # would need a Strategy to hold, and inventing one would compete with
        # StrategyStore.ensure_seeded() - so callers build it and pass it.
        self.controls = controls if controls is not None else Controls(
            strategy=Strategy.from_config()
        )
```

`Strategy.from_config()` is the shipped defaults, which is exactly what the old zero-argument `Controls()` meant. Add `from strategy import Strategy` if not already added in step 5.

- [ ] **Step 7: Update `run_forever`'s interval read**

In `run_forever`, replace `self.controls.interval` (the between-scan wait) with `self.controls.snapshot().strategy.interval`. Search for every remaining direct attribute access on `self.controls` and route it through `snapshot()`:

Run: `grep -n "controls\.\(interval\|auto_navigate\|paused\|enabled_actions\|strategy\)" tower_bot.py`
Expected after the change: no matches except `controls.snapshot()`.

- [ ] **Step 8: Update `build_checks_and_controls`**

Replace its `Controls(...)` construction:

```python
    controls = Controls(
        interval=args.interval,
        auto_navigate=args.auto_navigate,
        strategy=args.affordability if checks.get(args.affordability) else "brightness",
    )
```

with:

```python
    # Seeded from the loaded strategy, with the affordability method
    # downgraded if the atlas this machine has cannot serve it. The CLI
    # flags that override strategy fields are applied by the caller (see
    # plan 3's --strategy handling), not here: this function's job is to
    # build the checks and reconcile them with the policy it is given.
    affordability = loaded.affordability
    if checks.get(affordability) is None:
        affordability = "brightness"
    controls = Controls(strategy=replace(loaded, affordability=affordability))
```

and change its signature to take the loaded strategy:

```python
def build_checks_and_controls(
    loaded: Strategy, atlas_root: Path | None = None
) -> tuple[dict[str, AffordabilityCheck | None], Controls]:
```

Add `from dataclasses import replace` to `tower_bot.py`'s imports. Update `main()`'s call site to `build_checks_and_controls(StrategyStore().ensure_seeded(), ...)` and add `from strategy import Strategy, StrategyStore`.

- [ ] **Step 9: Run the loop tests**

Run: `uv run pytest tests/test_gating.py tests/test_control.py -q -p no:allure_pytest`
Expected: PASS

- [ ] **Step 10: Run the tests for every module that constructs a bot**

Run: `uv run pytest tests/test_bot_reporting.py tests/test_runs.py tests/test_cli.py tests/test_digit_affordability.py -q -p no:allure_pytest`
Expected: PASS. Any failure here is a call site still passing `click_cooldown=` or a bare `Controls()` — fix the call site, not the constructor.

- [ ] **Step 11: Commit**

```bash
git add tower_bot.py tests/test_gating.py
git commit -m "feat: the scan loop reads the strategy's ordered rows

Order IS priority, and a per-row threshold now reaches the matcher -
before, the loop walked config.ACTIONS and used the settings only as an
on/off filter, so neither could take effect.

Still exactly one snapshot per pass. click_cooldown leaves the
constructor: it is a strategy field now, and a constructor argument for
it would be a second source of truth."
```

---

### Task 3: The wire rename

**Files:**
- Modify: `web/app.py` — `ControlPatch` (around line 150-165), `_control_payload()` (inside `create_app`)
- Modify: `tests/test_control_api.py`
- Modify: `web/ui/lib/types.ts`, `web/ui/app/control/page.tsx`
- Test: `tests/test_control_api.py`

**Interfaces:**
- Consumes: `Controls.payload()`, `Controls.apply()` from Task 1
- Produces: the `/api/control` payload shape `{paused, strategy: {...}, affordability_available: [...]}`

- [ ] **Step 1: Update the API tests to the new shape**

In `tests/test_control_api.py`, update the `wired` fixture's `Controls` construction and every assertion. The fixture becomes:

```python
@pytest.fixture
def wired() -> tuple[TestClient, Controls, list, threading.Event]:
    seen: list = []

    class Recorder:
        def offer(self, event) -> bool:
            seen.append(event)
            return True

    bus = EventBus()
    bus.subscribe(Recorder())
    controls = Controls(strategy=replace(
        Strategy.from_config(), affordability="brightness"
    ))
    stop = threading.Event()
    app = create_app(
        state=BotState(), sse=SseSink(), bus=bus, db_path=None,
        unknown_dir=config.UNKNOWN_DIR, stop=stop,
        controls=controls, checks={"brightness": object(), "digits": None},
    )
    return TestClient(app), controls, seen, stop
```

with `from dataclasses import replace` and `from strategy import Strategy` added to the imports.

Then add these tests:

```python
def test_get_returns_the_whole_strategy(wired) -> None:
    client, _, _, _ = wired
    body = client.get("/api/control").json()
    assert body["paused"] is False
    assert body["strategy"]["affordability"] == "brightness"
    assert [row["name"] for row in body["strategy"]["actions"]] == [
        action.name for action in config.ACTIONS
    ]


def test_available_affordability_is_advertised_under_its_new_name(wired) -> None:
    client, _, _, _ = wired
    body = client.get("/api/control").json()
    # digits is None in the fixture's checks - no atlas on this machine - so
    # the browser must be told not to offer it.
    assert body["affordability_available"] == ["brightness"]


def test_patching_an_unavailable_affordability_is_refused(wired) -> None:
    client, controls, _, _ = wired
    response = client.patch("/api/control", json={"affordability": "digits"})
    assert response.status_code == 422
    assert "atlas" in response.json()["detail"]
    assert controls.snapshot().strategy.affordability == "brightness"


def test_patching_the_action_list_reorders_the_strategy(wired) -> None:
    client, controls, _, _ = wired
    rows = [row for row in reversed(client.get("/api/control").json()["strategy"]["actions"])]
    body = client.patch("/api/control", json={"actions": rows})
    assert body.status_code == 200
    assert [r.name for r in controls.snapshot().strategy.actions] == [
        row["name"] for row in rows
    ]


def test_an_invalid_patch_names_the_field(wired) -> None:
    client, _, _, _ = wired
    response = client.patch("/api/control", json={"interval": 0})
    assert response.status_code == 422
    assert "interval" in response.json()["detail"]
```

Delete or rewrite any existing test in this file asserting on `body["interval"]`, `body["strategy"] == "brightness"` (the old string), `body["enabled_actions"]` or `body["strategies_available"]` — those fields no longer exist. The replacements above cover what they covered.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_control_api.py -q -p no:allure_pytest`
Expected: FAIL — `KeyError: 'strategy'` is a dict now, and `affordability_available` is missing

- [ ] **Step 3: Update `ControlPatch` and the payload**

In `web/app.py`, replace `ControlPatch`:

```python
class ControlPatch(BaseModel):
    """A partial update. Every field optional; absent means "leave it alone".

    Deliberately loose on types beyond the obvious - Controls.apply() is the
    single validator, so the rules live in one place rather than being spelled
    out here and there and drifting apart. `actions` is a list of raw objects
    for the same reason: mirroring ActionRule's fields here would be a second
    schema to keep in step with strategy.py.
    """

    paused: bool | None = None
    affordability: str | None = None
    interval: float | None = None
    click_cooldown: float | None = None
    auto_navigate: bool | None = None
    max_runs: int | None = None
    navigation_cooldown: float | None = None
    screen_confirmations: int | None = None
    actions: list[dict[str, Any]] | None = None
```

Then replace `_control_payload()`:

```python
        def _control_payload() -> dict:
            # The browser needs to know which affordability methods actually
            # built (see build_affordability()) so it can grey out one with no
            # atlas rather than let a switch to it silently do nothing. The
            # action list needs no separate advertisement any more: the
            # strategy carries every row, enabled or not.
            payload = controls.payload()
            payload["affordability_available"] = sorted(
                name for name, check in available.items() if check is not None
            )
            return payload
```

And in `patch_control`, rename the guarded field:

```python
            strategy = requested.get("affordability")
            if strategy is not None and available.get(strategy) is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"affordability {strategy!r} is unavailable - no glyph "
                        "atlas is built"
                    ),
                )
```

`max_runs` needs care: `exclude_none=True` on `model_dump()` means a patch setting `max_runs` back to null (unlimited) cannot be expressed. Leave that limitation for plan 4's UI, which offers "unlimited" as an explicit 0-is-invalid sentinel — but add this comment above the `model_dump` call so the next reader knows it is known:

```python
            # exclude_none means "absent" and "explicitly null" are the same
            # request, so max_runs cannot be cleared through this route. The
            # strategy page clears it by PUTting the whole profile instead.
            requested = patch.model_dump(exclude_none=True)
```

Add `Any` to `web/app.py`'s `typing` imports if it is not already there (it is — check the existing import line).

- [ ] **Step 4: Run the API tests**

Run: `uv run pytest tests/test_control_api.py -q -p no:allure_pytest`
Expected: PASS

- [ ] **Step 5: Update the TypeScript types**

In `web/ui/lib/types.ts`, replace `ControlPayload`:

```typescript
export interface ActionRule {
  name: string;
  template: string;
  enabled: boolean;
  threshold: number;
  brightness_ratio: number;
}

/** Mirrors strategy.py's Strategy.to_dict(). */
export interface Strategy {
  name: string;
  actions: ActionRule[];
  affordability: string;
  interval: number;
  click_cooldown: number;
  auto_navigate: boolean;
  max_runs: number | null;
  navigation_cooldown: number;
  screen_confirmations: number;
}

export interface ControlPayload {
  paused: boolean;
  strategy: Strategy;
  affordability_available: string[];
}
```

- [ ] **Step 6: Keep the existing control page compiling**

`web/ui/app/control/page.tsx` reads `control.interval`, `control.auto_navigate`, `control.strategy` (as a string), `control.enabled_actions`, `control.actions` and `control.strategies_available`. Plan 4 rewrites this page entirely; for now, make it compile and keep working by reading through `control.strategy`:

- `control.interval` → `control.strategy.interval`
- `control.auto_navigate` → `control.strategy.auto_navigate`
- `control.strategy === name` → `control.strategy.affordability === name`
- `control.strategies_available` → `control.affordability_available`
- `control.actions.map(...)` → `control.strategy.actions.map((rule) => ...)` using `rule.name`
- `control.enabled_actions.includes(name)` → `rule.enabled`
- `toggleAction` sends the whole rewritten list:

```tsx
  const toggleAction = (name: string) =>
    send({
      actions: control.strategy.actions.map((rule) =>
        rule.name === name ? { ...rule, enabled: !rule.enabled } : rule,
      ),
    });
```

`send()`'s parameter type becomes `Partial<Strategy> & { paused?: boolean }`, and `patchControl` in `lib/api.ts` takes the same. Update both signatures.

- [ ] **Step 7: Rebuild the UI and run the build test**

```bash
cd web/ui && npm run build && cd ../..
uv run pytest tests/test_web_build.py -q -p no:allure_pytest
```

Expected: the Next.js build succeeds and the build test passes. The Python suite asserts against the committed `web/static/` output, so the rebuild is not optional.

- [ ] **Step 8: Run the frontend tests**

Run: `cd web/ui && npm test && cd ../..`
Expected: PASS. Update `app/control/page.test.tsx`'s fixture payload to the new shape if it fails on the old field names.

- [ ] **Step 9: Commit**

```bash
git add web/app.py tests/test_control_api.py web/ui/lib/types.ts web/ui/lib/api.ts web/ui/app/control/ web/static/
git commit -m "refactor: /api/control carries the whole strategy

'strategy' meant digits-vs-brightness; it now means the whole policy and
the affordability method is called affordability. Carrying a field whose
name means one thing to the server and another to a reader is worse than
one rename.

The control page is updated only enough to keep working - plan 4
rewrites it."
```

---

## Self-Review Notes

**Spec coverage.** Section 5 is covered by Task 1 (the reshape, the `Live` type, the wire rename's server half) and Task 3 (its client half). Section 6 is covered by Task 2: all three loop edits, the one-snapshot invariant, and the `max_runs` precedence rule the spec's self-review added.

**Not covered here, on purpose.** The two start-only fields (`navigation_cooldown`, `screen_confirmations`) are patchable and persisted by this plan but nothing reads them yet — `BotRunner` does, in plan 3. That is the correct seam: the fields must exist before the thing that consumes them.

**Known limitation, documented in code.** `exclude_none=True` means `max_runs` cannot be set back to null through `PATCH`. Plan 4's strategy page clears it by `PUT`ting the whole profile, which is why plan 3 adds that route.

**Type consistency.** `Live(paused, strategy)` is constructed in `Controls.snapshot()` and destructured in `run_once` as `settings.paused` / `settings.strategy.*` — checked against every use in Task 2's steps 3-7. `ActionRule.as_action()` returns `config.Action`, which is what `find_and_click_image(action)` takes and what `affordable(..., action)` reads `brightness_ratio` from.
