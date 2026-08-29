from __future__ import annotations

import events
from sinks.tui import TuiState


def stamped(bus: events.EventBus, event: events.Event) -> events.Event:
    return bus.publish(event)


def test_tracks_the_current_screen() -> None:
    bus, state = events.EventBus(), TuiState()

    state.apply(stamped(bus, events.ScreenChanged(
        prev="UNKNOWN", curr="IN_RUN", confidence=1.0, scores={})))

    assert state.screen == "IN_RUN"


def test_counts_taps_per_action() -> None:
    bus, state = events.EventBus(), TuiState()

    for _ in range(3):
        state.apply(stamped(bus, events.Tapped(action="Damage", x=1, y=2, score=0.9)))
    state.apply(stamped(bus, events.Tapped(action="Attack Speed", x=1, y=2, score=0.9)))

    assert state.taps == {"Damage": 3, "Attack Speed": 1}


def test_keeps_a_bounded_event_tail() -> None:
    bus, state = events.EventBus(), TuiState(tail=5)

    for _ in range(20):
        state.apply(stamped(bus, events.Navigated(target="RETRY")))

    assert len(state.tail) == 5


def test_counts_scans_and_records_the_last_error() -> None:
    bus, state = events.EventBus(), TuiState()

    state.apply(stamped(bus, events.ScanCompleted(screen="IN_RUN", duration_ms=12.0)))
    state.apply(stamped(bus, events.ScanCompleted(screen="IN_RUN", duration_ms=14.0)))
    state.apply(stamped(bus, events.BotError(message="screencap failed")))

    assert state.scans == 2
    assert state.screen == "IN_RUN"
    assert state.last_error == "screencap failed"
