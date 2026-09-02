from __future__ import annotations

import pytest

import events
from runs import RunTracker
from screens import ScreenState


def started(now: float = 100.0) -> RunTracker:
    """A tracker with run #1 open."""
    tracker = RunTracker()
    tracker.transition(ScreenState.IN_RUN, now=now)
    return tracker


def test_entering_a_run_from_the_menu_starts_one() -> None:
    tracker = RunTracker()
    tracker.transition(ScreenState.MAIN_MENU, now=90.0)

    event = tracker.transition(ScreenState.IN_RUN, now=100.0)

    assert isinstance(event, events.RunStarted)
    assert tracker.current_id == 1


def test_retry_starts_a_new_run_with_a_new_id() -> None:
    tracker = started()
    tracker.transition(ScreenState.GAME_OVER, now=200.0)

    event = tracker.transition(ScreenState.IN_RUN, now=210.0)

    assert isinstance(event, events.RunStarted)
    assert event.run_id == 2


def test_death_ends_the_run_and_records_its_duration() -> None:
    tracker = started()

    event = tracker.transition(ScreenState.GAME_OVER, now=250.0)

    assert isinstance(event, events.RunEnded)
    assert event.duration == 150.0
    assert event.abandoned is False
    assert tracker.completed == 1


def test_leaving_to_the_menu_abandons_the_run() -> None:
    tracker = started()

    event = tracker.transition(ScreenState.MAIN_MENU, now=120.0)

    assert isinstance(event, events.RunEnded)
    assert event.abandoned is True


def test_unknown_does_not_end_an_open_run() -> None:
    """A stray popup mid-battle must not fabricate a run boundary."""
    tracker = started()

    assert tracker.transition(ScreenState.UNKNOWN, now=110.0) is None
    assert tracker.current_id == 1


def test_death_without_a_started_run_is_ignored() -> None:
    """Starting the bot on the death screen must not invent a run."""
    tracker = RunTracker()

    assert tracker.transition(ScreenState.GAME_OVER, now=100.0) is None
    assert tracker.completed == 0
    assert tracker.current_id is None


# -- the two shipped bugs ---------------------------------------------------
def test_a_run_already_live_at_launch_is_still_tracked() -> None:
    """The tracker boots UNKNOWN. Launching the bot mid-run is the common
    case, not an edge case: the very first confirmed state is IN_RUN with no
    prior observation. That must still open a run, or the whole first run
    yields no RunStarted, no RunEnded and no `completed` increment - which
    silently turns `--max-runs N` into N+1 runs.
    """
    tracker = RunTracker()

    event = tracker.transition(ScreenState.IN_RUN, now=100.0)

    assert isinstance(event, events.RunStarted)
    assert event.run_id == 1
    assert tracker.current_id == 1

    ended = tracker.transition(ScreenState.GAME_OVER, now=160.0)
    assert isinstance(ended, events.RunEnded)
    assert ended.duration == 60.0
    assert tracker.completed == 1


def test_an_unknown_screen_straddling_the_boundary_still_closes_the_run() -> None:
    """IN_RUN -> UNKNOWN -> GAME_OVER must close run #1 exactly once.

    Ads and daily-reward popups land precisely between runs. If leaving
    UNKNOWN could not close a run, the id would survive into the next run and
    a later death would emit a RunEnded carrying the WRONG run_id and a
    duration spanning two runs. Corrupt history is worse than no history.
    """
    tracker = started()

    assert tracker.transition(ScreenState.UNKNOWN, now=110.0) is None

    event = tracker.transition(ScreenState.GAME_OVER, now=250.0)

    assert isinstance(event, events.RunEnded)
    assert event.run_id == 1
    assert event.abandoned is False
    assert event.duration == 150.0
    assert tracker.current_id is None
    assert tracker.completed == 1

    # ...and the next run gets its own id, not the stale one.
    nxt = tracker.transition(ScreenState.IN_RUN, now=260.0)
    assert isinstance(nxt, events.RunStarted)
    assert nxt.run_id == 2


def test_leaving_unknown_for_the_menu_abandons_the_open_run() -> None:
    tracker = started()
    tracker.transition(ScreenState.UNKNOWN, now=110.0)

    event = tracker.transition(ScreenState.MAIN_MENU, now=130.0)

    assert isinstance(event, events.RunEnded)
    assert event.abandoned is True
    assert tracker.current_id is None


def test_leaving_unknown_for_game_over_with_no_open_run_is_ignored() -> None:
    """The bot launched on an unmodelled screen and landed on the death
    modal. There was never a run to close, so nothing is emitted."""
    tracker = RunTracker()
    tracker.transition(ScreenState.UNKNOWN, now=90.0)

    assert tracker.transition(ScreenState.GAME_OVER, now=100.0) is None
    assert tracker.completed == 0
    assert tracker.current_id is None


def test_returning_to_in_run_from_unknown_does_not_reopen_the_run() -> None:
    """A popup mid-battle: the run stays open and keeps its id. It must not
    be renumbered, restarted, or double-counted."""
    tracker = started()
    tracker.transition(ScreenState.UNKNOWN, now=110.0)

    assert tracker.transition(ScreenState.IN_RUN, now=120.0) is None
    assert tracker.current_id == 1
    assert tracker.completed == 0

    # The original start time survives, so the duration spans the popup.
    event = tracker.transition(ScreenState.GAME_OVER, now=200.0)
    assert isinstance(event, events.RunEnded)
    assert event.run_id == 1
    assert event.duration == 100.0


def test_an_open_run_without_a_start_time_fails_loudly() -> None:
    """`_started_at` is set whenever `current_id` is. Papering over a broken
    invariant with `now` would silently emit duration=0.0 forever."""
    tracker = started()
    tracker._started_at = None  # corrupt the invariant

    with pytest.raises(RuntimeError):
        tracker.transition(ScreenState.GAME_OVER, now=200.0)


def test_run_ended_defaults_tier_to_none() -> None:
    tracker = RunTracker()
    tracker.transition(ScreenState.IN_RUN, now=0.0)
    ended = tracker.transition(ScreenState.GAME_OVER, now=10.0)
    assert ended.tier is None


def test_run_ids_can_be_seeded_so_a_restart_does_not_reuse_them() -> None:
    """Run ids are the database's primary key. Restarting at 1 would
    overwrite the previous session's runs one by one."""
    tracker = RunTracker(start_id=12)

    started = tracker.transition(ScreenState.IN_RUN, now=0.0)

    assert started.run_id == 12


def test_next_id_is_readable_so_a_restart_can_carry_it_forward() -> None:
    """A second bot in one process must not reissue the first bot's run ids.

    prepare_store() seeds the id once, at launch. Once the dashboard can
    start several bots without relaunching, the runner has to carry the
    counter across - and it needs a public way to read it.
    """
    from runs import RunTracker
    from screens import ScreenState

    tracker = RunTracker(start_id=5)
    assert tracker.next_id == 5
    tracker.transition(ScreenState.IN_RUN, now=0.0)
    assert tracker.current_id == 5
    assert tracker.next_id == 6


def test_a_fresh_tracker_seeded_from_next_id_does_not_collide() -> None:
    from runs import RunTracker
    from screens import ScreenState

    first = RunTracker(start_id=1)
    first.transition(ScreenState.IN_RUN, now=0.0)
    first.transition(ScreenState.GAME_OVER, now=1.0)

    second = RunTracker(start_id=first.next_id)
    second.transition(ScreenState.IN_RUN, now=2.0)
    assert second.current_id == 2
