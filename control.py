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

apply() does not parse a raw action list itself: Strategy.merged() does.
Validate-and-merge lives in the module that owns the type, next to
from_dict()'s own row parsing, so there is exactly one place that decides
what a raw action dict may contain - two parsers that must agree is a drift
risk neither module needs to carry.

This module is deliberately at the top level rather than under web/: the scan
loop depends on it, and the scan loop must not import the web layer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Mapping

from strategy import AFFORDABILITY, MAX_INTERVAL, MIN_INTERVAL, ControlError, Strategy

# Re-exported so `from control import ControlError` keeps working for every
# existing caller. It is DEFINED in strategy.py because control.py imports
# Strategy, and the reverse import would be a cycle.
__all__ = ["ControlError", "Controls", "Live", "STRATEGIES", "MIN_INTERVAL", "MAX_INTERVAL"]

# The affordability methods. Kept under the old name too because the CLI's
# --affordability choices and several tests still spell it this way.
STRATEGIES = AFFORDABILITY


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

        All-or-nothing, and structurally so rather than by discipline:
        Strategy.merged() builds a candidate whose constructor validates
        every field, and only a candidate that survives that is swapped in.
        A patch whose third field is invalid never touches the live object.

        Read, merge and commit all happen inside ONE lock acquisition -
        merged() is pure (a dict comprehension plus dataclasses.replace, no
        I/O), so there is no cost to holding the lock across it. Splitting
        the read from the commit - read self.paused/self.strategy, release,
        compute, re-acquire to write - was tried and is a real bug, not a
        style nit: a second apply() landing in the gap commits, and this
        call's re-acquire then sees `self.paused` disagree with the
        `was_paused` it captured before the release and "corrects" it back,
        silently reverting a field this patch never mentioned and reporting
        it in `changed` as though it had.
        """
        with self._lock:
            # bool() never raises, so `paused` has no separate validation
            # path the way interval/affordability/etc. do - there is no
            # value of it that could make this patch fail.
            staged_paused = bool(patch["paused"]) if "paused" in patch else self.paused
            candidate = self.strategy.merged(patch)

            changed: dict[str, Any] = {}
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
