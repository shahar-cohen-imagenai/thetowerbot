# Strategy model and store — Implementation Plan (1 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `strategy.py` — frozen `Strategy`/`ActionRule` value objects and a `StrategyStore` over `strategies/*.json` — with no callers yet.

**Architecture:** A pure model plus a filesystem store. `strategy.py` imports only `config` and the standard library; it knows nothing about threads, HTTP, or the scan loop. Value bounds are enforced in `__post_init__` so an invalid `Strategy` cannot exist; template existence is checked separately in `validated()` because it touches the disk. The store writes atomically (temp file + `os.replace`) and refuses profile names it would be unsafe to turn into paths.

**Tech Stack:** Python 3.12, dataclasses, `pytest`, `uv run`.

**Spec:** `docs/superpowers/specs/2026-09-02-bot-lifecycle-and-strategy-design.md` (sections 4 and 13)

## Global Constraints

- Every function and method carries type hints. `from __future__ import annotations` at the top of every module, matching every existing file.
- No module under the repo root may import from `web/`. `strategy.py` imports `config` and stdlib only.
- Run tests with `uv run pytest`. Never run the whole suite — run the specific file or test named in the step.
- Docstrings explain *why*, not *what*. Match the existing house style in `control.py` and `runs.py`.
- Nothing in this plan changes existing behaviour. `control.py`, `tower_bot.py` and `web/app.py` are untouched until plan 2.

---

## File Structure

| File | Responsibility |
|---|---|
| `strategy.py` (create) | `ControlError`, `ActionRule`, `Strategy`, `validate_name`, `StrategyStore` |
| `config.py` (modify) | add `STRATEGY_DIR` |
| `.gitignore` (modify) | nothing — `strategies/` is committed on purpose |
| `tests/test_strategy.py` (create) | the model: bounds, round-trip, seeding |
| `tests/test_strategy_store.py` (create) | the filesystem: atomicity, names, active pointer, delete guards |

Two test files rather than one because they fail for different reasons: the model's tests are pure and fast, the store's need `tmp_path`. Keeping them apart means a filesystem quirk never looks like a validation bug.

---

### Task 1: The value objects and their bounds

**Files:**
- Create: `strategy.py`
- Modify: `config.py` (append to the "Persistence" section)
- Test: `tests/test_strategy.py`

**Interfaces:**
- Consumes: `config.ACTIONS`, `config.Action`, `config.DEFAULT_THRESHOLD`, `config.DEFAULT_BRIGHTNESS_RATIO`, `config.SCAN_INTERVAL_SECONDS`, `config.CLICK_COOLDOWN_SECONDS`, `config.NAVIGATION_COOLDOWN_SECONDS`, `config.SCREEN_CONFIRMATIONS`
- Produces:
  - `strategy.ControlError(field: str, message: str)` with a `.field` attribute
  - `strategy.MIN_INTERVAL: float`, `strategy.MAX_INTERVAL: float`, `strategy.AFFORDABILITY: tuple[str, ...]`
  - `strategy.ActionRule(name, template, enabled=True, threshold=..., brightness_ratio=...)` with `.as_action() -> config.Action`
  - `strategy.Strategy(name, actions, affordability="digits", interval=..., click_cooldown=..., auto_navigate=False, max_runs=None, navigation_cooldown=..., screen_confirmations=...)`
  - `Strategy.from_config(name: str = "default") -> Strategy`

- [ ] **Step 1: Add the strategies directory to config.py**

Append to `config.py`, directly under the `DB_PATH` line in the Persistence section:

```python
# One JSON file per named strategy, plus a `.active` pointer. Committed, not
# gitignored: a strategy is a decision worth reviewing in a diff, and a fresh
# clone should start from the same defaults everyone else has.
STRATEGY_DIR: Path = Path(__file__).parent / "strategies"
```

- [ ] **Step 2: Write the failing tests for the value objects**

Create `tests/test_strategy.py`:

```python
"""The strategy value objects: a Strategy that exists is a Strategy in bounds.

Range and structure checks live in __post_init__ rather than in a validate()
someone can forget to call, so an out-of-range Strategy is unconstructible
rather than merely discouraged. Template existence is NOT checked here - it
touches the disk, so it lives in validated() and is tested in Task 2.
"""

from __future__ import annotations

import dataclasses

import pytest

import config
from strategy import ActionRule, ControlError, Strategy


def a_strategy(**overrides) -> Strategy:
    """A valid Strategy, with named fields overridden per test."""
    base = dict(
        name="test",
        actions=(ActionRule(name="Damage", template="upgrade_damage.png"),),
    )
    return Strategy(**{**base, **overrides})


def test_from_config_mirrors_the_shipped_actions() -> None:
    strategy = Strategy.from_config()
    assert strategy.name == "default"
    assert [rule.name for rule in strategy.actions] == [
        action.name for action in config.ACTIONS
    ]
    # Order is priority, so the round-trip must preserve it exactly.
    assert [rule.template for rule in strategy.actions] == [
        action.template for action in config.ACTIONS
    ]
    assert strategy.actions[0].threshold == config.ACTIONS[0].threshold


def test_a_rule_converts_to_the_action_the_loop_already_takes() -> None:
    rule = ActionRule(
        name="Damage", template="upgrade_damage.png",
        threshold=0.95, brightness_ratio=0.5,
    )
    action = rule.as_action()
    assert isinstance(action, config.Action)
    assert action.name == "Damage"
    assert action.template == "upgrade_damage.png"
    assert action.threshold == 0.95
    assert action.brightness_ratio == 0.5


def test_rules_and_strategies_are_frozen() -> None:
    # Frozen all the way down is what lets snapshot() stop copying: a caller
    # who is handed one cannot reach back into live state through it.
    strategy = a_strategy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        strategy.interval = 5.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        strategy.actions[0].threshold = 0.5


@pytest.mark.parametrize("interval", [0.0, 0.05, 3601.0, -1.0])
def test_interval_must_be_in_range(interval: float) -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(interval=interval)
    assert caught.value.field == "interval"


@pytest.mark.parametrize("threshold", [0.0, -0.1, 1.01])
def test_threshold_must_be_a_normalised_score(threshold: float) -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(actions=(ActionRule(name="D", template="d.png", threshold=threshold),))
    assert caught.value.field == "threshold"


def test_brightness_ratio_of_zero_is_legal() -> None:
    # config documents 0.0 as "disable the check", so it must not be rejected
    # along with the genuinely out-of-range values.
    strategy = a_strategy(
        actions=(ActionRule(name="D", template="d.png", brightness_ratio=0.0),)
    )
    assert strategy.actions[0].brightness_ratio == 0.0


@pytest.mark.parametrize("ratio", [-0.1, 1.5])
def test_brightness_ratio_must_be_a_fraction(ratio: float) -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(actions=(ActionRule(name="D", template="d.png", brightness_ratio=ratio),))
    assert caught.value.field == "brightness_ratio"


@pytest.mark.parametrize(
    "field,value",
    [
        ("click_cooldown", -1.0),
        ("click_cooldown", 61.0),
        ("navigation_cooldown", -1.0),
        ("navigation_cooldown", 61.0),
        ("screen_confirmations", 0),
        ("screen_confirmations", 11),
        ("max_runs", 0),
        ("max_runs", -5),
        ("affordability", "vibes"),
    ],
)
def test_out_of_range_fields_name_themselves(field: str, value: object) -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(**{field: value})
    # The field name is what the browser renders beside the offending input,
    # so it has to be the real one, not a generic "invalid strategy".
    assert caught.value.field == field


def test_zero_cooldowns_are_legal() -> None:
    strategy = a_strategy(click_cooldown=0.0, navigation_cooldown=0.0)
    assert strategy.click_cooldown == 0.0


def test_max_runs_may_be_unlimited() -> None:
    assert a_strategy(max_runs=None).max_runs is None
    assert a_strategy(max_runs=1).max_runs == 1


def test_actions_must_be_non_empty() -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(actions=())
    assert caught.value.field == "actions"


def test_action_names_must_be_unique() -> None:
    # A duplicate name makes Tapped.action ambiguous in the event log and in
    # the per-action tap tallies, which key on it.
    with pytest.raises(ControlError) as caught:
        a_strategy(actions=(
            ActionRule(name="Damage", template="a.png"),
            ActionRule(name="Damage", template="b.png"),
        ))
    assert caught.value.field == "actions"


def test_actions_are_normalised_to_a_tuple() -> None:
    # from_dict hands in a list; the loop must never receive something a
    # caller could append to.
    strategy = a_strategy(actions=[ActionRule(name="D", template="d.png")])
    assert isinstance(strategy.actions, tuple)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_strategy.py -q -p no:allure_pytest`
Expected: collection error — `ModuleNotFoundError: No module named 'strategy'`

- [ ] **Step 4: Write strategy.py**

Create `strategy.py`:

```python
"""What the bot buys, how fast it scans, and when it stops.

The policy the scan loop reads, as a value object rather than a module of
constants. Frozen all the way down - a Strategy holds a tuple of frozen
ActionRules - which is what lets Controls.snapshot() hand one straight to
another thread without the defensive copying a mutable structure would need.

Range and structure checks run in __post_init__, so an out-of-range Strategy
cannot be constructed at all. Template existence is deliberately NOT checked
there: it touches the filesystem, and a value object should not do I/O to
know whether it is well formed. That check lives in validated(), which the
store and the HTTP layer call.

Imports config and the standard library, and nothing else. The scan loop
depends on this module, so this module must not depend on the web layer.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import config

# A floor because a zero or negative interval is a busy loop against ADB, and
# a ceiling because an hour between scans is indistinguishable from a hang.
MIN_INTERVAL = 0.1
MAX_INTERVAL = 3600.0

AFFORDABILITY = ("digits", "brightness")

# Longest a cooldown may be. Zero is legal - it means "no cooldown" - but a
# minute between two taps on one button is not a strategy, it is a typo.
MAX_COOLDOWN = 60.0

# The debounce depth. 0 would defeat the screen tracker entirely; double
# figures would make a transition take most of a minute at a 2s interval.
MIN_CONFIRMATIONS = 1
MAX_CONFIRMATIONS = 10


class ControlError(ValueError):
    """A value the caller may not set. `field` names the offending key.

    Defined here rather than in control.py because control.py imports this
    module for Strategy - the reverse import would be a cycle. control.py
    re-exports it, so `from control import ControlError` keeps working for
    every existing caller.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _in_range(field_name: str, value: float, low: float, high: float) -> None:
    if not low <= value <= high:
        raise ControlError(field_name, f"{field_name} must be between {low} and {high}")


@dataclass(frozen=True)
class ActionRule:
    """One upgrade the bot may buy, and how sure it must be before buying.

    Mirrors config.Action, plus `enabled`. The two stay separate because
    Action is what vision and affordability take, and ActionRule is what a
    human edits - as_action() is the whole adapter between them.
    """

    name: str
    template: str
    enabled: bool = True
    threshold: float = config.DEFAULT_THRESHOLD
    brightness_ratio: float = config.DEFAULT_BRIGHTNESS_RATIO

    def __post_init__(self) -> None:
        if not self.name:
            raise ControlError("name", "an action needs a name")
        if not self.template:
            raise ControlError("template", f"{self.name} needs a template file")
        # Exclusive at zero: a threshold of 0 matches literally anything, so
        # the bot would tap wherever the template happened to correlate best.
        if not 0.0 < self.threshold <= 1.0:
            raise ControlError("threshold", "threshold must be above 0 and at most 1")
        # Inclusive at zero, unlike threshold: config documents 0.0 as
        # "disable the brightness check", which is a real choice.
        _in_range("brightness_ratio", self.brightness_ratio, 0.0, 1.0)

    def as_action(self) -> config.Action:
        """The shape find_and_click_image() and affordable() already take."""
        return config.Action(
            name=self.name,
            template=self.template,
            threshold=self.threshold,
            brightness_ratio=self.brightness_ratio,
        )


@dataclass(frozen=True)
class Strategy:
    """The whole decision policy, as one immutable value.

    `actions` order IS priority. A separate priority field would be a second
    way to say what the tuple already says, and the two would drift the first
    time a row was inserted.
    """

    name: str
    actions: tuple[ActionRule, ...]
    affordability: str = "digits"
    interval: float = config.SCAN_INTERVAL_SECONDS
    click_cooldown: float = config.CLICK_COOLDOWN_SECONDS
    auto_navigate: bool = False
    max_runs: int | None = None
    # Read by BotRunner when it builds the bot, not by the scan loop: both of
    # these configure a stateful collaborator (ScreenTracker's debounce depth,
    # Navigator's rate limit) that is constructed once and carries state
    # across scans. Changing them under a running tracker has no correct
    # answer, so the dashboard labels them "applies on next Start".
    navigation_cooldown: float = config.NAVIGATION_COOLDOWN_SECONDS
    screen_confirmations: int = config.SCREEN_CONFIRMATIONS

    def __post_init__(self) -> None:
        # Normalise before validating: from_dict hands in a list, and the loop
        # must never be given something a caller could append to.
        object.__setattr__(self, "actions", tuple(self.actions))

        if not self.actions:
            raise ControlError("actions", "a strategy needs at least one action")
        names = [rule.name for rule in self.actions]
        if len(set(names)) != len(names):
            raise ControlError("actions", "action names must be unique")
        if self.affordability not in AFFORDABILITY:
            raise ControlError(
                "affordability", f"affordability must be one of {AFFORDABILITY}"
            )
        _in_range("interval", self.interval, MIN_INTERVAL, MAX_INTERVAL)
        _in_range("click_cooldown", self.click_cooldown, 0.0, MAX_COOLDOWN)
        _in_range("navigation_cooldown", self.navigation_cooldown, 0.0, MAX_COOLDOWN)
        _in_range(
            "screen_confirmations",
            self.screen_confirmations,
            MIN_CONFIRMATIONS,
            MAX_CONFIRMATIONS,
        )
        if self.max_runs is not None and self.max_runs < 1:
            raise ControlError("max_runs", "max_runs must be null or at least 1")

    @classmethod
    def from_config(cls, name: str = "default") -> "Strategy":
        """The shipped defaults, as a Strategy.

        Keeps config.ACTIONS meaningful: it stays the origin of the defaults -
        what a fresh clone starts from - without staying the source of truth.
        """
        return cls(
            name=name,
            actions=tuple(
                ActionRule(
                    name=action.name,
                    template=action.template,
                    threshold=action.threshold,
                    brightness_ratio=action.brightness_ratio,
                )
                for action in config.ACTIONS
            ),
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_strategy.py -q -p no:allure_pytest`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add strategy.py config.py tests/test_strategy.py
git commit -m "feat: add Strategy and ActionRule value objects

Range and structure checks run in __post_init__, so an out-of-range
Strategy is unconstructible rather than merely discouraged. Frozen all
the way down, which is what will let Controls.snapshot() stop copying.

ControlError moves here rather than staying in control.py: control.py
will import Strategy, so the reverse import would be a cycle."
```

---

### Task 2: JSON round-trip and the template check

**Files:**
- Modify: `strategy.py`
- Test: `tests/test_strategy.py` (append)

**Interfaces:**
- Consumes: `ActionRule`, `Strategy`, `ControlError` from Task 1
- Produces:
  - `Strategy.to_dict() -> dict[str, Any]`
  - `Strategy.from_dict(raw: Mapping[str, Any]) -> Strategy` (classmethod)
  - `Strategy.validated(template_dir: Path | None = None) -> Strategy`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_strategy.py`:

```python
def test_dict_round_trip_preserves_everything() -> None:
    original = a_strategy(
        interval=3.5, auto_navigate=True, max_runs=7,
        affordability="brightness", click_cooldown=0.5,
        navigation_cooldown=4.0, screen_confirmations=3,
        actions=(
            ActionRule(name="Damage", template="d.png", threshold=0.95),
            ActionRule(name="Speed", template="s.png", enabled=False),
        ),
    )
    assert Strategy.from_dict(original.to_dict()) == original


def test_to_dict_is_json_serialisable() -> None:
    import json
    # The store writes this straight to a file; a tuple that json cannot
    # encode would only show up at save time.
    text = json.dumps(a_strategy().to_dict())
    assert "actions" in text


def test_from_dict_rejects_an_unknown_key() -> None:
    # A typo in a hand-edited file must not be silently ignored - that is
    # exactly the case where the file says one thing and the bot does another.
    raw = a_strategy().to_dict()
    raw["intervall"] = 5.0
    with pytest.raises(ControlError) as caught:
        Strategy.from_dict(raw)
    assert caught.value.field == "intervall"


def test_from_dict_rejects_a_missing_required_key() -> None:
    raw = a_strategy().to_dict()
    del raw["actions"]
    with pytest.raises(ControlError) as caught:
        Strategy.from_dict(raw)
    assert caught.value.field == "actions"


def test_from_dict_rejects_a_wrong_type_with_the_field_name() -> None:
    raw = a_strategy().to_dict()
    raw["interval"] = "fast"
    with pytest.raises(ControlError) as caught:
        Strategy.from_dict(raw)
    assert caught.value.field == "interval"


def test_validated_accepts_templates_that_exist(tmp_path) -> None:
    (tmp_path / "d.png").write_bytes(b"")
    strategy = a_strategy(actions=(ActionRule(name="D", template="d.png"),))
    assert strategy.validated(template_dir=tmp_path) is strategy


def test_validated_rejects_a_missing_template(tmp_path) -> None:
    """A mistyped filename does not fail loudly on its own.

    TemplateCache.get() raises when the loop first reaches that row, deep
    inside a scan, and the bot then reports an error every pass forever.
    Rejecting it at save turns a recurring runtime failure into one 422.
    """
    strategy = a_strategy(actions=(ActionRule(name="D", template="nope.png"),))
    with pytest.raises(ControlError) as caught:
        strategy.validated(template_dir=tmp_path)
    assert caught.value.field == "template"
    assert "nope.png" in str(caught.value)


def test_validated_checks_disabled_rows_too(tmp_path) -> None:
    # A disabled row is one checkbox away from running. Letting it hold a
    # broken template just moves the failure to whenever it gets switched on.
    strategy = a_strategy(
        actions=(ActionRule(name="D", template="nope.png", enabled=False),)
    )
    with pytest.raises(ControlError):
        strategy.validated(template_dir=tmp_path)


def test_the_shipped_default_passes_its_own_template_check() -> None:
    # If this fails, config.ACTIONS references a template that is not in the
    # repo - which would make ensure_seeded() write an unloadable profile.
    Strategy.from_config().validated()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_strategy.py -q -p no:allure_pytest -k "dict or validated or shipped_default"`
Expected: FAIL with `AttributeError: type object 'Strategy' has no attribute 'from_dict'`

- [ ] **Step 3: Implement the round-trip and the template check**

Append these methods to `Strategy` in `strategy.py`, after `from_config`:

```python
    def to_dict(self) -> dict[str, Any]:
        """JSON-shaped. What the store writes and the browser receives."""
        return {
            "name": self.name,
            "actions": [
                {
                    "name": rule.name,
                    "template": rule.template,
                    "enabled": rule.enabled,
                    "threshold": rule.threshold,
                    "brightness_ratio": rule.brightness_ratio,
                }
                for rule in self.actions
            ],
            "affordability": self.affordability,
            "interval": self.interval,
            "click_cooldown": self.click_cooldown,
            "auto_navigate": self.auto_navigate,
            "max_runs": self.max_runs,
            "navigation_cooldown": self.navigation_cooldown,
            "screen_confirmations": self.screen_confirmations,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Strategy":
        """Parse a stored or posted strategy, naming any field it gets wrong.

        Unknown keys are an error, not something to ignore: a typo in a
        hand-edited file is exactly the case where the file says one thing
        and the bot does another, which is what this design exists to stop.
        """
        known = {f.name for f in dataclasses.fields(cls)}
        for key in raw:
            if key not in known:
                raise ControlError(key, f"unknown field {key!r}")
        for required in ("name", "actions"):
            if required not in raw:
                raise ControlError(required, f"{required} is required")

        rules: list[ActionRule] = []
        rule_fields = {f.name for f in dataclasses.fields(ActionRule)}
        for entry in raw["actions"]:
            for key in entry:
                if key not in rule_fields:
                    raise ControlError(key, f"unknown action field {key!r}")
            try:
                rules.append(ActionRule(**entry))
            except TypeError as exc:
                raise ControlError("actions", str(exc)) from None

        values = {key: raw[key] for key in raw if key != "actions"}
        try:
            return cls(actions=tuple(rules), **values)
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ControlError):
                raise
            # A wrong type reaches here as a bare TypeError from the
            # dataclass; find which field it was so the browser can point at
            # the right input rather than the whole form.
            raise ControlError(_offending_field(values), str(exc)) from None

    def validated(self, template_dir: Path | None = None) -> "Strategy":
        """Check what construction could not: that the templates are on disk.

        Returns self so it can be used inline - `store.save(s.validated())`.
        Disabled rows are checked too: a disabled row is one checkbox away
        from running, and letting it hold a broken template only moves the
        failure to whenever someone switches it on.
        """
        root = template_dir if template_dir is not None else config.TEMPLATE_DIR
        for rule in self.actions:
            if not (root / rule.template).is_file():
                raise ControlError(
                    "template",
                    f"{rule.name}: no template file {rule.template!r} in {root}",
                )
        return self
```

Add the `_offending_field` helper above the `ActionRule` class:

```python
def _offending_field(values: Mapping[str, Any]) -> str:
    """Which key made the dataclass raise a bare TypeError.

    dataclasses report a type mismatch without saying which field, and the
    browser needs a field name to highlight the right input. Re-check each
    value against the annotation to find it; fall back to the whole object
    when nothing obvious is wrong.
    """
    expected: dict[str, tuple[type, ...]] = {
        "name": (str,),
        "affordability": (str,),
        "interval": (int, float),
        "click_cooldown": (int, float),
        "navigation_cooldown": (int, float),
        "screen_confirmations": (int,),
        "auto_navigate": (bool,),
    }
    for key, types in expected.items():
        if key in values and not isinstance(values[key], types):
            return key
    if "max_runs" in values and values["max_runs"] is not None:
        if not isinstance(values["max_runs"], int):
            return "max_runs"
    return "strategy"
```

Add `import dataclasses` to the imports at the top of `strategy.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_strategy.py -q -p no:allure_pytest`
Expected: PASS (all tests, including Task 1's)

- [ ] **Step 5: Commit**

```bash
git add strategy.py tests/test_strategy.py
git commit -m "feat: JSON round-trip and template validation for Strategy

Unknown keys are an error rather than ignored - a typo in a hand-edited
file is exactly the case where the file says one thing and the bot does
another. Template existence is checked in validated() rather than
__post_init__ because it touches the disk."
```

---

### Task 3: The store — atomic saves and safe names

**Files:**
- Modify: `strategy.py`
- Test: `tests/test_strategy_store.py` (create)

**Interfaces:**
- Consumes: `Strategy`, `ControlError` from Tasks 1-2
- Produces:
  - `strategy.validate_name(name: str) -> str`
  - `strategy.StrategyStore(directory: Path = config.STRATEGY_DIR)` with `.names() -> list[str]`, `.load(name: str) -> Strategy`, `.save(strategy: Strategy) -> None`, `.path_for(name: str) -> Path`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_store.py`:

```python
"""The filesystem half of strategy.py.

Kept apart from tests/test_strategy.py on purpose: the model's tests are
pure, these need tmp_path, and a filesystem quirk should never look like a
validation bug.
"""

from __future__ import annotations

import json

import pytest

from strategy import ControlError, Strategy, StrategyStore, validate_name


@pytest.fixture
def store(tmp_path, monkeypatch) -> StrategyStore:
    """A store over tmp_path, whose templates all exist.

    save() runs validated(), so the fixture points TEMPLATE_DIR at a
    directory holding the files config.ACTIONS names - otherwise every save
    in this file would fail for a reason that has nothing to do with the
    store.
    """
    import config

    templates = tmp_path / "templates"
    templates.mkdir()
    for action in config.ACTIONS:
        (templates / action.template).write_bytes(b"")
    monkeypatch.setattr(config, "TEMPLATE_DIR", templates)
    return StrategyStore(tmp_path / "strategies")


def test_saving_then_loading_returns_an_equal_strategy(store) -> None:
    original = Strategy.from_config("mine")
    store.save(original)
    assert store.load("mine") == original


def test_save_creates_the_directory(tmp_path, store) -> None:
    # First run of a fresh clone: strategies/ does not exist yet, and a save
    # must not be the thing that discovers that.
    store.save(Strategy.from_config("mine"))
    assert (tmp_path / "strategies" / "mine.json").is_file()


def test_saved_json_is_human_readable(store) -> None:
    # These files are meant to be read in a diff and edited by hand, so the
    # formatting is part of the contract, not an accident of json.dumps.
    store.save(Strategy.from_config("mine"))
    text = store.path_for("mine").read_text()
    assert text.endswith("\n")
    assert "\n  " in text  # indented, not one line


def test_names_are_sorted_and_exclude_the_active_pointer(store) -> None:
    store.save(Strategy.from_config("zeta"))
    store.save(Strategy.from_config("alpha"))
    (store.directory / ".active").write_text("alpha\n")
    assert store.names() == ["alpha", "zeta"]


def test_names_is_empty_before_anything_is_saved(store) -> None:
    assert store.names() == []


def test_loading_an_absent_strategy_names_the_field(store) -> None:
    with pytest.raises(ControlError) as caught:
        store.load("ghost")
    assert caught.value.field == "name"


def test_loading_unparseable_json_names_the_file(store) -> None:
    store.directory.mkdir(parents=True, exist_ok=True)
    store.path_for("broken").write_text("{not json")
    with pytest.raises(ControlError) as caught:
        store.load("broken")
    assert caught.value.field == "name"
    assert "broken" in str(caught.value)


def test_save_is_atomic(store, monkeypatch) -> None:
    """A crash mid-write must not leave a profile that fails to parse.

    Simulated by making os.replace raise: the temp file may survive, but the
    real path must be untouched - which is exactly the guarantee a
    write-then-rename gives and a write-in-place does not.
    """
    import os

    store.save(Strategy.from_config("mine"))
    before = store.path_for("mine").read_text()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save(Strategy.from_config("mine").__class__(
            **{**Strategy.from_config("mine").to_dict(),
               "actions": Strategy.from_config("mine").actions,
               "interval": 9.0}
        ))
    assert store.path_for("mine").read_text() == before


def test_save_leaves_no_temp_files_behind(store) -> None:
    store.save(Strategy.from_config("mine"))
    assert [p.name for p in store.directory.iterdir() if p.suffix != ".json"] == []


def test_save_rejects_a_strategy_with_a_missing_template(store, monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "TEMPLATE_DIR", store.directory / "empty")
    with pytest.raises(ControlError):
        store.save(Strategy.from_config("mine"))


@pytest.mark.parametrize(
    "name",
    ["../escape", "/etc/passwd", "a/b", "", "x" * 65, "has space", "dot.name", ".."],
)
def test_unsafe_names_are_refused_before_they_become_paths(name: str) -> None:
    """A name arrives off a URL and becomes a filename.

    /api/unknown/{name} already learned this: resolve-and-check is the
    fallback, refusing the name outright is the fix.
    """
    with pytest.raises(ControlError) as caught:
        validate_name(name)
    assert caught.value.field == "name"


@pytest.mark.parametrize("name", ["default", "crit-build", "early_game", "T5", "x" * 64])
def test_reasonable_names_are_accepted(name: str) -> None:
    assert validate_name(name) == name


def test_the_store_refuses_unsafe_names_on_every_path(store) -> None:
    with pytest.raises(ControlError):
        store.load("../../etc/passwd")
    with pytest.raises(ControlError):
        store.path_for("../escape")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_strategy_store.py -q -p no:allure_pytest`
Expected: FAIL with `ImportError: cannot import name 'StrategyStore' from 'strategy'`

- [ ] **Step 3: Implement `validate_name` and `StrategyStore`**

Append to `strategy.py`:

```python
# A profile name becomes a filename, and arrives off a URL. Anything outside
# this set is refused before it is ever joined to a path.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_name(name: str) -> str:
    """Return `name` if it is safe to turn into a filename, else raise.

    Refusing outright rather than resolving-and-checking: the set of names
    worth having is small and obvious, and a rule you can read in one line
    has nowhere for a traversal to hide.
    """
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise ControlError(
            "name",
            "a strategy name must be 1-64 characters of letters, digits, "
            "'-' or '_'",
        )
    return name


class StrategyStore:
    """Named strategies as JSON files in one directory.

    No caching. A profile is read when it is asked for, so a file edited by
    hand while the dashboard is open is picked up on the next load rather
    than at the next restart.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (
            directory if directory is not None else config.STRATEGY_DIR
        )

    def path_for(self, name: str) -> Path:
        return self.directory / f"{validate_name(name)}.json"

    def names(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        # Sorted so the browser's list is stable between polls, and so two
        # listings compare equal when nothing changed.
        return sorted(p.stem for p in self.directory.glob("*.json"))

    def load(self, name: str) -> Strategy:
        path = self.path_for(name)
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            raise ControlError("name", f"no strategy named {name!r}") from None
        except json.JSONDecodeError as exc:
            raise ControlError("name", f"{name}.json is not valid JSON: {exc}") from None
        return Strategy.from_dict(raw)

    def save(self, strategy: Strategy) -> None:
        """Validate, then write atomically.

        Temp file in the SAME directory, then os.replace: replace is atomic
        within a filesystem, and a same-directory temp file is what
        guarantees there is only one filesystem involved. A crash mid-write
        can leave the temp file behind, but never a half-written profile that
        fails to parse on next launch.
        """
        strategy.validated()
        validate_name(strategy.name)
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.path_for(strategy.name)
        # Named from the target so two concurrent saves of *different*
        # profiles cannot collide on one temp path.
        tmp = target.with_name(f".{target.name}.tmp")
        try:
            # Indented and newline-terminated: these files are meant to be
            # read in a diff and edited by hand.
            tmp.write_text(json.dumps(strategy.to_dict(), indent=2) + "\n")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
```

Note: `names()` uses `glob("*.json")`, which never matches `.active` — the pointer file has no `.json` suffix, so it is excluded without a special case.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_strategy_store.py -q -p no:allure_pytest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add strategy.py tests/test_strategy_store.py
git commit -m "feat: add StrategyStore with atomic saves and safe names

Temp file in the same directory plus os.replace, so a crash mid-write
cannot leave a profile that fails to parse. Names are refused against a
pattern before they are joined to a path rather than resolved and
checked afterwards."
```

---

### Task 4: The active pointer, seeding, and delete guards

**Files:**
- Modify: `strategy.py`
- Modify: `.gitignore` (add the temp-file pattern)
- Test: `tests/test_strategy_store.py` (append)

**Interfaces:**
- Consumes: `StrategyStore` from Task 3
- Produces:
  - `StrategyStore.active_name() -> str`
  - `StrategyStore.set_active(name: str) -> None`
  - `StrategyStore.delete(name: str) -> None`
  - `StrategyStore.ensure_seeded() -> Strategy`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_strategy_store.py`:

```python
def test_ensure_seeded_writes_the_shipped_defaults(store) -> None:
    seeded = store.ensure_seeded()
    assert seeded == Strategy.from_config("default")
    assert store.names() == ["default"]
    assert store.active_name() == "default"


def test_ensure_seeded_is_idempotent_and_does_not_overwrite(store) -> None:
    """A second launch must not undo your edits.

    This is the failure that would be worst to discover late: seeding on
    every start would silently reset a tuned profile back to config.ACTIONS.
    """
    store.ensure_seeded()
    edited = Strategy.from_config("default")
    edited = Strategy.from_dict({**edited.to_dict(), "interval": 9.0})
    store.save(edited)

    again = store.ensure_seeded()
    assert again.interval == 9.0
    assert store.load("default").interval == 9.0


def test_ensure_seeded_returns_the_active_profile_not_the_default(store) -> None:
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    store.set_active("crit")
    assert store.ensure_seeded().name == "crit"


def test_ensure_seeded_recovers_from_an_active_pointer_at_a_deleted_file(store) -> None:
    """The pointer can outlive its target - someone deletes a file by hand.

    Falling back to any surviving profile beats refusing to start: the bot
    is more useful running the wrong strategy than not running.
    """
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    store.set_active("crit")
    store.path_for("crit").unlink()

    recovered = store.ensure_seeded()
    assert recovered.name == "default"
    assert store.active_name() == "default"


def test_set_active_persists_across_stores(store) -> None:
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    store.set_active("crit")
    # A fresh store over the same directory is what a restart looks like.
    assert StrategyStore(store.directory).active_name() == "crit"


def test_set_active_refuses_a_name_with_no_file(store) -> None:
    store.ensure_seeded()
    with pytest.raises(ControlError) as caught:
        store.set_active("ghost")
    assert caught.value.field == "name"
    assert store.active_name() == "default"


def test_delete_removes_a_profile(store) -> None:
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    store.delete("crit")
    assert store.names() == ["default"]


def test_delete_refuses_the_active_profile(store) -> None:
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    with pytest.raises(ControlError) as caught:
        store.delete("default")
    assert caught.value.field == "name"
    assert store.names() == ["crit", "default"]


def test_delete_refuses_the_last_profile(store) -> None:
    # Both guards exist because both leave the bot with no policy to load,
    # which has no recovery short of hand-editing the directory.
    store.ensure_seeded()
    store.set_active("default")
    with pytest.raises(ControlError):
        store.delete("default")


def test_delete_refuses_an_absent_profile(store) -> None:
    store.ensure_seeded()
    store.save(Strategy.from_config("crit"))
    with pytest.raises(ControlError):
        store.delete("ghost")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_strategy_store.py -q -p no:allure_pytest -k "seeded or active or delete"`
Expected: FAIL with `AttributeError: 'StrategyStore' object has no attribute 'ensure_seeded'`

- [ ] **Step 3: Implement the pointer, seeding and deletion**

Add to `StrategyStore` in `strategy.py`:

```python
    # Hidden, and with no .json suffix, so it can neither collide with a
    # profile named "active" nor be picked up by names()'s glob.
    _ACTIVE = ".active"

    @property
    def _active_path(self) -> Path:
        return self.directory / self._ACTIVE

    def active_name(self) -> str:
        """Which profile the bot loads. Falls back rather than failing.

        A pointer can outlive its target - someone deletes a file by hand -
        and a bot that refuses to start because of a stale one-line file is
        worse than one that picks a surviving profile and says so.
        """
        try:
            name = self._active_path.read_text().strip()
        except FileNotFoundError:
            name = ""
        if name and self.path_for_exists(name):
            return name
        remaining = self.names()
        return remaining[0] if remaining else "default"

    def path_for_exists(self, name: str) -> bool:
        """True if `name` is both safe and on disk."""
        try:
            return self.path_for(name).is_file()
        except ControlError:
            return False

    def set_active(self, name: str) -> None:
        if not self.path_for_exists(name):
            raise ControlError("name", f"no strategy named {name!r}")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._active_path.write_text(f"{name}\n")

    def delete(self, name: str) -> None:
        """Remove a profile, refusing the two states with no recovery."""
        if not self.path_for_exists(name):
            raise ControlError("name", f"no strategy named {name!r}")
        if name == self.active_name():
            raise ControlError(
                "name", f"{name!r} is active - activate another strategy first"
            )
        if len(self.names()) <= 1:
            raise ControlError("name", "the last strategy cannot be deleted")
        self.path_for(name).unlink()

    def ensure_seeded(self) -> Strategy:
        """Guarantee a loadable active profile, and return it.

        Called once at startup. Writes default.json from config.ACTIONS only
        when the directory holds nothing - seeding on every launch would
        silently reset a tuned profile back to the shipped defaults, which is
        the worst version of this bug to find late.
        """
        if not self.names():
            self.save(Strategy.from_config("default"))
        name = self.active_name()
        # active_name() already fell back to a surviving profile if the
        # pointer was stale or absent; write that choice down so the next
        # reader agrees with this one rather than falling back again.
        current = ""
        if self._active_path.is_file():
            current = self._active_path.read_text().strip()
        if current != name:
            self.set_active(name)
        return self.load(name)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_strategy_store.py tests/test_strategy.py -q -p no:allure_pytest`
Expected: PASS (both files)

- [ ] **Step 5: Seed the repo's own strategies directory**

```bash
uv run python -c "
from strategy import StrategyStore
print(StrategyStore().ensure_seeded().name)
"
cat strategies/default.json
```

Expected: prints `default`, then a readable JSON file with the four upgrades from `config.ACTIONS` in order.

- [ ] **Step 6: Ignore the temp files, commit the profiles**

Append to `.gitignore`:

```gitignore
# Atomic-save temp files. The profiles themselves ARE committed - a strategy
# is a decision worth reviewing in a diff.
strategies/.*.tmp
```

- [ ] **Step 7: Commit**

```bash
git add strategy.py tests/test_strategy_store.py .gitignore strategies/
git commit -m "feat: active-profile pointer, seeding and delete guards

ensure_seeded() writes default.json only when the directory is empty:
seeding every launch would silently reset a tuned profile back to
config.ACTIONS. active_name() falls back to a surviving profile rather
than failing, because a stale one-line pointer must not stop the bot."
```

---

## Self-Review Notes

**Spec coverage.** Section 4 of the spec is fully covered: the value objects and `order IS priority` (Task 1), the validation table and the template-existence argument (Tasks 1-2), the store's six methods, atomic save, the `.active` dotfile, name sanitisation and `ensure_seeded` (Tasks 3-4). Section 13's "`strategies/` is a new write path" is set up here but only becomes true for the *web layer* in plan 3, where the docstring change belongs.

**Deferred to later plans, deliberately.** `ControlError`'s re-export from `control.py` (plan 2, where `control.py` is rewritten), every HTTP route (plan 3), and the `Live` snapshot type (plan 2). This plan adds no callers, so the suite stays green throughout.

**Type consistency.** `ControlError(field, message)` and `.field` match `control.py`'s existing signature exactly, so plan 2's re-export is a rename-free move. `ActionRule.as_action()` returns `config.Action`, which is what `find_and_click_image()` already takes.
