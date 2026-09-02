"""What the bot buys, how fast it scans, and when it stops.

The policy the scan loop reads, as a value object rather than a module of
constants. Frozen all the way down - a Strategy holds a tuple of frozen
ActionRules - which is what lets Controls.snapshot() hand one straight to
another thread without the defensive copying a mutable structure would need.

Range and structure checks run in __post_init__, so an out-of-range Strategy
cannot be constructed at all. Template existence is deliberately NOT checked
there: it touches the filesystem, and a value object should not do I/O to
know whether it is well formed. That check belongs to a later validated()
step, once one exists.

Imports config and the standard library, and nothing else. The scan loop
depends on this module, so this module must not depend on the web layer.
"""

from __future__ import annotations

from dataclasses import dataclass

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
