from __future__ import annotations

import events
from runs import RunTracker
from screens import ScreenState


def test_entering_a_run_from_the_menu_starts_one() -> None:
    tracker = RunTracker()

    event = tracker.transition(ScreenState.MAIN_MENU, ScreenState.IN_RUN, now=100.0)

    assert isinstance(event, events.RunStarted)
    assert tracker.current_id == 1


def test_retry_starts_a_new_run_with_a_new_id() -> None:
    tracker = RunTracker()
    tracker.transition(ScreenState.MAIN_MENU, ScreenState.IN_RUN, now=100.0)
    tracker.transition(ScreenState.IN_RUN, ScreenState.GAME_OVER, now=200.0)

    event = tracker.transition(ScreenState.GAME_OVER, ScreenState.IN_RUN, now=210.0)

    assert isinstance(event, events.RunStarted)
    assert event.run_id == 2


def test_death_ends_the_run_and_records_its_duration() -> None:
    tracker = RunTracker()
    tracker.transition(ScreenState.MAIN_MENU, ScreenState.IN_RUN, now=100.0)

    event = tracker.transition(ScreenState.IN_RUN, ScreenState.GAME_OVER, now=250.0)

    assert isinstance(event, events.RunEnded)
    assert event.duration == 150.0
    assert event.abandoned is False
    assert tracker.completed == 1


def test_leaving_to_the_menu_abandons_the_run() -> None:
    tracker = RunTracker()
    tracker.transition(ScreenState.MAIN_MENU, ScreenState.IN_RUN, now=100.0)

    event = tracker.transition(ScreenState.IN_RUN, ScreenState.MAIN_MENU, now=120.0)

    assert isinstance(event, events.RunEnded)
    assert event.abandoned is True


def test_unknown_does_not_end_an_open_run() -> None:
    """A stray popup mid-battle must not fabricate a run boundary."""
    tracker = RunTracker()
    tracker.transition(ScreenState.MAIN_MENU, ScreenState.IN_RUN, now=100.0)

    assert tracker.transition(ScreenState.IN_RUN, ScreenState.UNKNOWN, now=110.0) is None
    assert tracker.current_id == 1

    assert tracker.transition(ScreenState.UNKNOWN, ScreenState.IN_RUN, now=120.0) is None
    assert tracker.current_id == 1


def test_death_without_a_started_run_is_ignored() -> None:
    """Starting the bot on the death screen must not invent a run."""
    tracker = RunTracker()

    assert tracker.transition(ScreenState.UNKNOWN, ScreenState.GAME_OVER, now=100.0) is None
    assert tracker.completed == 0
