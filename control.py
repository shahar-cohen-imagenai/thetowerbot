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
