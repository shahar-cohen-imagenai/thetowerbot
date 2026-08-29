from __future__ import annotations

import pytest

from screens import ScreenReading, ScreenState, ScreenTracker


def reading(state: ScreenState) -> ScreenReading:
    return ScreenReading(state, confidence=1.0, scores={})


def test_starts_unknown() -> None:
    assert ScreenTracker().state is ScreenState.UNKNOWN


def test_a_single_reading_does_not_transition() -> None:
    tracker = ScreenTracker(confirmations=2)
    assert tracker.observe(reading(ScreenState.MAIN_MENU)) is None
    assert tracker.state is ScreenState.UNKNOWN


def test_two_consecutive_readings_confirm_the_transition() -> None:
    tracker = ScreenTracker(confirmations=2)
    tracker.observe(reading(ScreenState.MAIN_MENU))

    assert tracker.observe(reading(ScreenState.MAIN_MENU)) is ScreenState.MAIN_MENU
    assert tracker.state is ScreenState.MAIN_MENU


def test_an_interrupted_sequence_does_not_transition() -> None:
    """A mid-fade frame between two stable ones must not slip through."""
    tracker = ScreenTracker(confirmations=2)
    tracker.observe(reading(ScreenState.MAIN_MENU))
    tracker.observe(reading(ScreenState.MAIN_MENU))

    tracker.observe(reading(ScreenState.IN_RUN))
    tracker.observe(reading(ScreenState.GAME_OVER))  # interrupts the run of IN_RUN

    assert tracker.state is ScreenState.MAIN_MENU


def test_staying_on_the_same_screen_reports_no_transition() -> None:
    tracker = ScreenTracker(confirmations=2)
    tracker.observe(reading(ScreenState.IN_RUN))
    tracker.observe(reading(ScreenState.IN_RUN))

    assert tracker.observe(reading(ScreenState.IN_RUN)) is None
    assert tracker.observe(reading(ScreenState.IN_RUN)) is None


def test_consecutive_transitions_both_fire() -> None:
    tracker = ScreenTracker(confirmations=2)
    for _ in range(2):
        tracker.observe(reading(ScreenState.MAIN_MENU))
    for _ in range(2):
        result = tracker.observe(reading(ScreenState.IN_RUN))

    assert result is ScreenState.IN_RUN
    assert tracker.state is ScreenState.IN_RUN


def test_unknown_is_a_state_like_any_other() -> None:
    """UNKNOWN still needs confirming, so one bad frame cannot halt the bot."""
    tracker = ScreenTracker(confirmations=2)
    for _ in range(2):
        tracker.observe(reading(ScreenState.IN_RUN))

    assert tracker.observe(reading(ScreenState.UNKNOWN)) is None
    assert tracker.state is ScreenState.IN_RUN

    assert tracker.observe(reading(ScreenState.UNKNOWN)) is ScreenState.UNKNOWN
