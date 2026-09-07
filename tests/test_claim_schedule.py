"""The claim cadence, as a pure rule.

No device, no clock, no database: every input is a parameter, so every branch
here is reachable from a synthetic state. `None` means "never claimed", which
is deliberately not the same fact as "claimed at epoch 0".
"""
from __future__ import annotations

import pytest

from claim_schedule import ClaimState, due

EIGHT_HOURS = 8.0
HOUR = 3600.0


def state(**kwargs: object) -> ClaimState:
    base: dict[str, object] = {
        "last_missions": None,
        "last_milestones": None,
        "best_wave": None,
        "claimed_best_wave": None,
    }
    base.update(kwargs)
    return ClaimState(**base)  # type: ignore[arg-type]


def test_a_never_claimed_account_owes_missions_immediately() -> None:
    """None is not epoch 0. An account nobody has claimed for is due now."""
    assert due(state(), now=0.0, missions_every_hours=EIGHT_HOURS,
               milestones_on_new_best=True) == "missions"


def test_missions_are_not_due_again_inside_the_window() -> None:
    assert due(state(last_missions=1000.0), now=1000.0 + 7 * HOUR,
               missions_every_hours=EIGHT_HOURS, milestones_on_new_best=True) is None


def test_missions_come_due_exactly_on_the_window() -> None:
    """On the boundary, not after it: an 8h cadence that fires only past 8h
    drifts one scan later every cycle."""
    assert due(state(last_missions=1000.0), now=1000.0 + 8 * HOUR,
               missions_every_hours=EIGHT_HOURS, milestones_on_new_best=True) == "missions"


def test_a_new_personal_best_owes_milestones() -> None:
    assert due(state(last_missions=1000.0, best_wave=30, claimed_best_wave=16),
               now=1000.0, missions_every_hours=EIGHT_HOURS,
               milestones_on_new_best=True) == "milestones"


def test_an_unimproved_best_owes_nothing() -> None:
    assert due(state(last_missions=1000.0, best_wave=16, claimed_best_wave=16),
               now=1000.0, missions_every_hours=EIGHT_HOURS,
               milestones_on_new_best=True) is None


def test_a_ladder_never_claimed_owes_milestones() -> None:
    """claimed_best_wave None means the ladder has never been claimed at all -
    which is exactly this account's live state."""
    assert due(state(last_missions=1000.0, best_wave=16, claimed_best_wave=None),
               now=1000.0, missions_every_hours=EIGHT_HOURS,
               milestones_on_new_best=True) == "milestones"


def test_an_unread_best_wave_owes_nothing_rather_than_guessing() -> None:
    """best_wave None is "nobody has read it", not "wave 0". Spending a menu
    trip on an unknown is the coerce-to-zero mistake."""
    assert due(state(last_missions=1000.0, best_wave=None, claimed_best_wave=16),
               now=1000.0, missions_every_hours=EIGHT_HOURS,
               milestones_on_new_best=True) is None


def test_milestones_outrank_missions_when_both_are_due() -> None:
    """Milestones pay 1,660 coins / 235 gems / 5 stones once; missions pay 3
    gems and come back in 8 hours. Only one walk may be armed at a time, so
    the order is a real decision, not a tie-break."""
    assert due(state(last_missions=None, best_wave=30, claimed_best_wave=16),
               now=0.0, missions_every_hours=EIGHT_HOURS,
               milestones_on_new_best=True) == "milestones"


def test_disabling_the_milestone_trigger_leaves_missions_alone() -> None:
    assert due(state(last_missions=None, best_wave=30, claimed_best_wave=16),
               now=0.0, missions_every_hours=EIGHT_HOURS,
               milestones_on_new_best=False) == "missions"


@pytest.mark.parametrize("hours", [0.0, -1.0, float("inf"), float("nan")])
def test_an_unusable_cadence_owes_nothing_rather_than_firing_every_scan(hours: float) -> None:
    """A zero or non-finite window would arm a walk on every single frame."""
    assert due(state(), now=0.0, missions_every_hours=hours,
               milestones_on_new_best=False) is None


@pytest.mark.parametrize("now", [float("inf"), float("nan")])
def test_a_non_finite_clock_owes_nothing(now: float) -> None:
    assert due(state(), now=now, missions_every_hours=EIGHT_HOURS,
               milestones_on_new_best=False) is None


def test_a_clock_that_went_backwards_does_not_fire() -> None:
    """A last-claim dated in the future is a corrupt or restored state, not a
    due claim. Waiting is the safe reading; the window will pass eventually."""
    assert due(state(last_missions=10_000.0), now=1000.0,
               missions_every_hours=EIGHT_HOURS, milestones_on_new_best=False) is None


@pytest.mark.parametrize("hours", [0.0, -1.0, float("inf"), float("nan")])
def test_milestones_override_unusable_missions_cadence(hours: float) -> None:
    """A broken missions cadence must not suppress a due milestone claim.

    The milestones check runs before the missions_every_hours validity guard
    in due(). This ordering is deliberate: milestone due-ness is independent
    of the missions cadence, so garbage cadence values must not prevent a
    legitimately-due milestone from being returned.
    """
    assert due(state(best_wave=30, claimed_best_wave=16),
               now=0.0, missions_every_hours=hours,
               milestones_on_new_best=True) == "milestones"
