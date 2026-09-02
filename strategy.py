"""What the bot buys, how fast it scans, and when it stops.

The policy the scan loop reads, as a value object rather than a module of
constants. Frozen all the way down - a Strategy holds a tuple of frozen
ActionRules - which is what lets Controls.snapshot() hand one straight to
another thread without the defensive copying a mutable structure would need.

Range and structure checks run in __post_init__, so an out-of-range Strategy
cannot be constructed at all. Template existence is deliberately NOT checked
there: it touches the filesystem, and a value object should not do I/O to
know whether it is well formed. Template validation lives in the validated()
method instead, so that a disk-I/O check runs only when explicitly requested.

Imports config and the standard library, and nothing else. The scan loop
depends on this module, so this module must not depend on the web layer.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from dataclasses import dataclass
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

    Defined here rather than in control.py because control.py will need to
    import this module for Strategy - the reverse import would be a cycle.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _in_range(field_name: str, value: float, low: float, high: float) -> None:
    if not low <= value <= high:
        raise ControlError(field_name, f"{field_name} must be between {low} and {high}")


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
        # Normalise before validating: from_dict will hand in a list, and the
        # loop must never be given something a caller could append to.
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
    def from_config(cls, name: str = "default") -> Strategy:
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
    def from_dict(cls, raw: Mapping[str, Any]) -> Strategy:
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

        # Validate that actions is a list or tuple before iterating
        if not isinstance(raw["actions"], (list, tuple)):
            raise ControlError(
                "actions",
                f"actions must be a list or tuple, not {type(raw['actions']).__name__!r}",
            )

        for entry in raw["actions"]:
            # Validate that each action entry is a Mapping (dict-like)
            if not isinstance(entry, dict):
                raise ControlError(
                    "actions",
                    f"each action must be a dict, not {type(entry).__name__!r}",
                )
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

    def validated(self, template_dir: Path | None = None) -> Strategy:
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


# A profile name becomes a filename, and arrives off a URL. Anything outside
# this set is refused before it is ever joined to a path.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_name(name: str) -> str:
    """Return `name` if it is safe to turn into a filename, else raise.

    Refusing outright rather than resolving-and-checking: the set of names
    worth having is small and obvious, and a rule you can read in one line
    has nowhere for a traversal to hide.
    """
    # fullmatch, not match: `$` matches at end-of-string OR just before a
    # trailing "\n", and .match() does not require consuming the rest of the
    # string either way - "mine\n" would slip through as an "accepted" name
    # and land in a filename with a literal newline in it.
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
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
        # Not bound as a default argument: that would evaluate
        # config.STRATEGY_DIR once at import time, and a test that
        # monkeypatches the constant afterwards would never be seen.
        self.directory = (
            directory if directory is not None else config.STRATEGY_DIR
        )

    def path_for(self, name: str) -> Path:
        return self.directory / f"{validate_name(name)}.json"

    def names(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        # Sorted so the browser's list is stable between polls, and so two
        # listings compare equal when nothing changed. glob("*.json") never
        # matches the .active pointer file, so it is excluded without a
        # special case.
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
        # Redundant with path_for()'s own call below, but deliberately kept:
        # this one runs before mkdir, so an invalid name fails before it can
        # create the strategies directory as a side effect.
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
        # No mkdir: path_for_exists(name) can only be true if self.directory
        # already holds name.json, so the directory is guaranteed to exist.
        if not self.path_for_exists(name):
            raise ControlError("name", f"no strategy named {name!r}")
        self._active_path.write_text(f"{name}\n")

    def delete(self, name: str) -> None:
        """Remove a profile, refusing the two states with no recovery.

        Order matters: the last-profile guard runs before the active-profile
        guard. A lone profile is necessarily the active one, so checking
        active-first would make the last-profile branch unreachable - the
        "last strategy" message would never fire, and a test written to
        cover it would exercise the active guard instead without saying so.
        """
        if not self.path_for_exists(name):
            raise ControlError("name", f"no strategy named {name!r}")
        if len(self.names()) <= 1:
            raise ControlError("name", "the last strategy cannot be deleted")
        if name == self.active_name():
            raise ControlError(
                "name", f"{name!r} is active - activate another strategy first"
            )
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
