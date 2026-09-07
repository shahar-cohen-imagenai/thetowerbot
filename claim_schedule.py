"""Which free claim is owed, as a pure rule over a state and a clock.

No device, no database and no clock of its own: `now` and the last-claim
times are parameters, so the whole policy is decidable from a synthetic state
and the loop's call site stays the only place that knows the real time.

`None` is used throughout for "nobody has read or done this", which is
deliberately not the same fact as a zero. An unread best wave owes nothing
rather than a guess.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

ClaimKind = Literal["missions", "milestones"]

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class ClaimState:
    """Everything the cadence needs, and nothing it does not.

    `claimed_best_wave` is the wave the ladder was last claimed at, not the
    last reward taken: the MILESTONES page has no per-claim counter, so
    "have we claimed since we got further" is the only question the evidence
    can answer. None means the ladder has never been claimed - the live
    account's actual state.
    """

    last_missions: float | None
    last_milestones: float | None
    best_wave: int | None
    claimed_best_wave: int | None


def due(
    state: ClaimState,
    *,
    now: float,
    missions_every_hours: float,
    milestones_on_new_best: bool,
) -> ClaimKind | None:
    """The one claim to offer next, or None.

    Returns at most ONE kind because at most one maintenance walk may be
    armed at a time - every BotRunner.request_* refuses against every other
    walk's .active. Returning a list would invite a caller to arm two and
    silently lose the second to a 409.

    Milestones outrank missions: the ladder pays 1,660 coins, 235 gems and 5
    stones once, missions pay 3 gems and come back in eight hours.
    """
    if not math.isfinite(now):
        return None

    if milestones_on_new_best and state.best_wave is not None:
        # None claimed_best_wave means never claimed, which any read best
        # wave beats. Not coerced to 0 - that would reach the same answer by
        # accident rather than by rule.
        if state.claimed_best_wave is None or state.best_wave > state.claimed_best_wave:
            return "milestones"

    if not math.isfinite(missions_every_hours) or missions_every_hours <= 0:
        # A zero or non-finite window would arm a walk on every frame.
        return None
    if state.last_missions is None:
        return "missions"
    if not math.isfinite(state.last_missions):
        return None
    elapsed = now - state.last_missions
    if elapsed < 0:
        # The clock went backwards, or a restored state is dated ahead. Not a
        # due claim; waiting is the only safe reading.
        return None
    if elapsed >= missions_every_hours * SECONDS_PER_HOUR:
        return "missions"
    return None
