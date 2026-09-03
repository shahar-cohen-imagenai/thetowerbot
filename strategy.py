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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import config

# A floor because a zero or negative interval is a busy loop against ADB, and
# a ceiling because an hour between scans is indistinguishable from a hang.
MIN_INTERVAL = 0.1
MAX_INTERVAL = 3600.0

AFFORDABILITY = ("digits", "brightness")

# Fields a PATCH may set directly on a Strategy via merged(). `name` is
# absent on purpose: renaming a profile is the store's business (save under
# a new name), not a live edit to the running policy. `actions` is absent
# too - it is handled separately because replacing it means re-parsing a raw
# row list, not copying a scalar.
PATCHABLE_FIELDS = (
    "affordability",
    "interval",
    "click_cooldown",
    "auto_navigate",
    "max_runs",
    "navigation_cooldown",
    "screen_confirmations",
)

# Longest a cooldown may be. Zero is legal - it means "no cooldown" - but a
# minute between two taps on one button is not a strategy, it is a typo.
MAX_COOLDOWN = 60.0

# The debounce depth. 0 would defeat the screen tracker entirely; double
# figures would make a transition take most of a minute at a 2s interval.
MIN_CONFIRMATIONS = 1
MAX_CONFIRMATIONS = 10


class ControlError(ValueError):
    """A value the caller may not set.

    `field` names the offending key, so the browser can highlight the input
    that caused it. `code` names the *kind* of failure - "not_found",
    "conflict", or the default "invalid" - so an HTTP layer can map one to
    404, 409 or 422 without string-matching the message text. A message is
    for a human to read; a status must never depend on its wording.

    Defined here rather than in control.py because control.py will need to
    import this module for Strategy - the reverse import would be a cycle.
    """

    def __init__(self, field: str, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.field = field
        self.code = code


def _in_range(field_name: str, value: float, low: float, high: float) -> None:
    if not low <= value <= high:
        raise ControlError(field_name, f"{field_name} must be between {low} and {high}")


# Dataclasses do not enforce their annotations, and this policy is fed
# arbitrary client JSON. `enabled="no"` is truthy, so without these tables a
# row the user switched OFF keeps being bought - silent wrong behaviour, not
# a validation nicety. One table per class, shared between __post_init__
# (which enforces it) and _offending_field (which reads it to name a field).
_RULE_TYPES: dict[str, tuple[type, ...]] = {
    "name": (str,),
    "template": (str,),
    "enabled": (bool,),
    "threshold": (int, float),
    "brightness_ratio": (int, float),
}

_STRATEGY_TYPES: dict[str, tuple[type, ...]] = {
    "name": (str,),
    "affordability": (str,),
    "interval": (int, float),
    "click_cooldown": (int, float),
    "navigation_cooldown": (int, float),
    "screen_confirmations": (int,),
    "auto_navigate": (bool,),
    "max_runs": (int,),
}

# Fields whose declared type includes None, so None is not a type error.
_OPTIONAL = frozenset({"max_runs"})


def _has_type(value: Any, types: tuple[type, ...]) -> bool:
    # bool is a subclass of int in Python, so an unguarded isinstance check
    # would let max_runs=True through as a run limit of 1.
    if bool not in types and isinstance(value, bool):
        return False
    return isinstance(value, types)


def _wrong_type(
    values: Mapping[str, Any], expected: Mapping[str, tuple[type, ...]]
) -> str | None:
    """The first key in `values` whose type contradicts `expected`, if any."""
    for key, types in expected.items():
        if key not in values:
            continue
        value = values[key]
        if value is None and key in _OPTIONAL:
            continue
        if not _has_type(value, types):
            return key
    return None


def _check_types(
    values: Mapping[str, Any], expected: Mapping[str, tuple[type, ...]]
) -> None:
    key = _wrong_type(values, expected)
    if key is not None:
        wanted = " or ".join(t.__name__ for t in expected[key])
        if key in _OPTIONAL:
            wanted += " or null"
        got = type(values[key]).__name__
        raise ControlError(key, f"{key} must be {wanted}, not {got}")


def _own_values(instance: Any) -> dict[str, Any]:
    """The instance's fields as a plain dict.

    Not dataclasses.asdict(): that deep-copies and recurses into nested
    dataclasses, and all these checks need is a shallow look at each field.
    """
    return {f.name: getattr(instance, f.name) for f in dataclasses.fields(instance)}


def _offending_field(values: Mapping[str, Any]) -> str:
    """Which key made the dataclass raise a bare TypeError.

    dataclasses report a type mismatch without saying which field, and the
    browser needs a field name to highlight the right input. Re-check each
    value against the annotation to find it; fall back to the whole object
    when nothing obvious is wrong.
    """
    return _wrong_type(values, _STRATEGY_TYPES) or "strategy"


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
        # Types before values: the emptiness and range checks below all
        # assume the field is already the type it claims to be.
        _check_types(_own_values(self), _RULE_TYPES)
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


def _parse_action_rows(raw: Any) -> tuple[ActionRule, ...]:
    """Turn a raw action list into ActionRules, naming what it got wrong.

    The one place that parses a client-supplied action row - used by both
    from_dict() (a whole document) and Strategy.merged() (a partial patch).
    Two independent per-row parsers is how "unknown field" ends up meaning
    two different things depending which endpoint you hit; this way there is
    only one meaning to keep straight.
    """
    if not isinstance(raw, (list, tuple)):
        raise ControlError(
            "actions", f"actions must be a list or tuple, not {type(raw).__name__!r}"
        )
    rule_fields = {f.name for f in dataclasses.fields(ActionRule)}
    rules: list[ActionRule] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ControlError(
                "actions",
                f"each action must be a mapping, not {type(entry).__name__!r}",
            )
        for key in entry:
            if key not in rule_fields:
                raise ControlError(key, f"unknown action field {key!r}")
        try:
            rules.append(ActionRule(**entry))
        except TypeError as exc:
            raise ControlError("actions", str(exc)) from None
    return tuple(rules)


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

        # Types before values, for the same reason as in ActionRule: _in_range
        # on a str raises a bare TypeError, and "no" in `auto_navigate` is
        # truthy rather than wrong.
        _check_types(_own_values(self), _STRATEGY_TYPES)

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

        rules = _parse_action_rows(raw["actions"])

        values = {key: raw[key] for key in raw if key != "actions"}
        try:
            return cls(actions=rules, **values)
        except ControlError:
            raise
        except (TypeError, ValueError) as exc:
            # Anything that gets past __post_init__'s own checks and still
            # fails - a missing or duplicated argument, say - arrives as a
            # bare TypeError; find which field it was so the browser can
            # point at the right input rather than the whole form.
            raise ControlError(_offending_field(values), str(exc)) from None

    def merged(self, patch: Mapping[str, Any]) -> Strategy:
        """Validate `patch` against this Strategy and return the result.

        The validate-and-merge half of Controls.apply(): unlike from_dict(),
        which parses a whole document and rejects anything it does not
        recognise, this ignores keys outside PATCHABLE_FIELDS and "actions"
        rather than erroring on them - Controls.apply() feeds it session
        state (like `paused`) that this type deliberately does not own, and
        that is not a typo to reject, just a field for someone else.

        What patch DOES touch is still validated whole: the candidate goes
        through the same constructor - and so the same __post_init__ - as
        every other Strategy, so a bad field never reaches self. Reuses
        _parse_action_rows for "actions" rather than parsing rows itself, so
        a raw action dict means the same thing here as it does in
        from_dict() - one parser, not two that must be kept in step.
        """
        updates: dict[str, Any] = {
            key: patch[key] for key in PATCHABLE_FIELDS if key in patch
        }
        if "actions" in patch:
            updates["actions"] = _parse_action_rows(patch["actions"])
        if not updates:
            return self
        try:
            return dataclasses.replace(self, **updates)
        except TypeError as exc:
            # Belt and braces with __post_init__'s own checks: PATCHABLE_FIELDS
            # only ever names real fields, so this should be unreachable, but
            # a bare TypeError with no field name is worse than a defensive
            # catch that names one.
            raise ControlError(_offending_field(updates), str(exc)) from None

    def validated(self, template_dir: Path | None = None) -> Strategy:
        """Check what construction could not: that the templates are on disk.

        Returns self so it can be used inline - `store.save(s.validated())`.
        Disabled rows are checked too: a disabled row is one checkbox away
        from running, and letting it hold a broken template only moves the
        failure to whenever someone switches it on.

        A template arrives in the same client JSON as the profile name and is
        joined to a path just as the name is, so it gets the same suspicion:
        it must name a file INSIDE the template directory, not merely a file
        that exists. vision.py passes an absolute template straight to
        cv2.imread, so an unchecked one lets the request body choose which
        file on disk the bot reads.
        """
        root = template_dir if template_dir is not None else config.TEMPLATE_DIR
        for rule in self.actions:
            # Belt and braces with ActionRule's own type check: this runs on
            # whatever self holds, and frozen is not the same as unreachable.
            if not isinstance(rule.template, str):
                raise ControlError(
                    "template", f"{rule.name}: template must be a filename"
                )
            full = root / rule.template
            # Containment, not a no-separators rule: config.NAV_TARGETS uses
            # names like "nav/claim.png", so a legitimate template may live in
            # a subdirectory. What must never be legitimate is leaving the
            # directory - an absolute path (which Path.__truediv__ silently
            # takes whole, discarding root) or a "../" walk out of it.
            inside = full.resolve().is_relative_to(root.resolve())
            if Path(rule.template).is_absolute() or not inside:
                raise ControlError(
                    "template", f"{rule.name}: template must be inside {root}"
                )
            if not full.is_file():
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

    # Hidden, and with no .json suffix, so it can neither collide with a
    # profile named "active" nor be picked up by names()'s glob.
    _ACTIVE = ".active"

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
        #
        # Filtered through load()'s own rule so the listing and the loader
        # agree: a hand-placed "my strategy.json" is a valid glob hit whose
        # stem validate_name refuses, and offering a name that cannot then be
        # opened is worse than omitting a file nobody could have opened.
        found: list[str] = []
        for path in self.directory.glob("*.json"):
            try:
                found.append(validate_name(path.stem))
            except ControlError:
                continue
        return sorted(found)

    def load(self, name: str) -> Strategy:
        path = self.path_for(name)
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            raise ControlError(
                "name", f"no strategy named {name!r}", "not_found"
            ) from None
        except json.JSONDecodeError as exc:
            raise ControlError("name", f"{name}.json is not valid JSON: {exc}") from None
        return Strategy.from_dict(raw)

    def save(self, strategy: Strategy) -> None:
        """Validate, then write atomically.

        Temp file in the SAME directory, then os.replace: replace is atomic
        within a filesystem, and a same-directory temp file is what
        guarantees there is only one filesystem involved. What this
        guarantees is that a reader of `name.json` always sees a complete
        document - either the previous save's or this one's. It does not
        order concurrent writers: two saves of the same profile still race,
        and the last replace wins whole.

        No lock, because the writers that matter are not in one process: the
        bot runs alongside the web server, so a threading.Lock would guard
        the half of the problem that is already the smaller half. Making
        every writer's temp path unique is what keeps the race to "one of
        the two documents wins" instead of "the two interleave into one".
        """
        strategy.validated()
        # Redundant with path_for()'s own call below, but deliberately kept:
        # this one runs before mkdir, so an invalid name fails before it can
        # create the strategies directory as a side effect.
        validate_name(strategy.name)
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.path_for(strategy.name)
        # Unique per writer, not merely per target: a later stage saves on
        # every settings change, so two saves of the SAME profile are the
        # expected case. Sharing one temp path would let their write_text
        # calls interleave into a single spliced file, and leave whichever
        # writer replaced second with a FileNotFoundError from the other's
        # finally clause. The .gitignore pattern "strategies/.*.tmp" still
        # matches this name.
        tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            # Indented and newline-terminated: these files are meant to be
            # read in a diff and edited by hand.
            tmp.write_text(json.dumps(strategy.to_dict(), indent=2) + "\n")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)

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
        if name and self.exists(name):
            return name
        remaining = self.names()
        return remaining[0] if remaining else "default"

    def exists(self, name: str) -> bool:
        """True if `name` is both safe and on disk."""
        try:
            return self.path_for(name).is_file()
        except ControlError:
            return False

    def set_active(self, name: str) -> None:
        # No mkdir: exists(name) can only be true if self.directory already
        # holds name.json, so the directory is guaranteed to exist.
        if not self.exists(name):
            raise ControlError("name", f"no strategy named {name!r}", "not_found")
        self._active_path.write_text(f"{name}\n")

    def delete(self, name: str) -> None:
        """Remove a profile, refusing the two states with no recovery.

        Order matters: the last-profile guard runs before the active-profile
        guard. A lone profile is necessarily the active one, so checking
        active-first would make the last-profile branch unreachable - the
        "last strategy" message would never fire, and a test written to
        cover it would exercise the active guard instead without saying so.
        """
        if not self.exists(name):
            raise ControlError("name", f"no strategy named {name!r}", "not_found")
        if len(self.names()) <= 1:
            raise ControlError(
                "name", "the last strategy cannot be deleted", "conflict"
            )
        if name == self.active_name():
            raise ControlError(
                "name",
                f"{name!r} is active - activate another strategy first",
                "conflict",
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

        # active_name() checks that the pointer's target exists, not that it
        # parses, so its answer can still be a corrupt file - and a bot that
        # will not start because one profile was hand-edited badly is the
        # same failure the pointer fallback above exists to avoid. Try the
        # pointer first, then every surviving profile, and take the first
        # that loads. dict.fromkeys keeps that order without retrying the
        # pointer's own profile a second time.
        tried: list[str] = []
        for name in dict.fromkeys([self.active_name(), *self.names()]):
            tried.append(name)
            try:
                strategy = self.load(name)
            except ControlError:
                continue
            # Whatever was fallen back to, write it down so the next reader
            # agrees with this one rather than falling back again.
            current = ""
            if self._active_path.is_file():
                current = self._active_path.read_text().strip()
            if current != name:
                self.set_active(name)
            return strategy

        # Nothing on disk parses. Raising beats quietly seeding over the top:
        # a silent reseed is indistinguishable from a tuned profile having
        # been reset, and it would overwrite the very file whose contents the
        # owner still needs in order to repair it.
        raise ControlError(
            "name",
            f"no loadable strategy in {self.directory} (tried {', '.join(tried)})",
            "not_found",
        )
