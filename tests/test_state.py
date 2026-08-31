from __future__ import annotations

import events
from sinks.state import BotState


def stamped(event: events.Event) -> events.Event:
    return events.EventBus().publish(event)


def test_a_run_opens_and_closes_the_current_run_panel() -> None:
    state = BotState()

    state.apply(stamped(events.RunStarted(run_id=4)))
    assert state.snapshot()["run"]["id"] == 4

    state.apply(stamped(events.RunEnded(run_id=4, duration=60.0)))
    assert state.snapshot()["run"] is None
    assert state.snapshot()["runs_completed"] == 1


def test_taps_are_counted_both_overall_and_for_the_current_run() -> None:
    state = BotState()
    state.apply(stamped(events.Tapped(action="Damage", x=1, y=2, score=0.9)))
    state.apply(stamped(events.RunStarted(run_id=1)))
    state.apply(stamped(events.Tapped(action="Damage", x=1, y=2, score=0.9)))

    snapshot = state.snapshot()

    assert snapshot["taps"]["Damage"] == 2
    assert snapshot["run"]["taps"]["Damage"] == 1


def test_the_wallet_tracks_the_latest_scan() -> None:
    state = BotState()
    state.apply(stamped(events.ScanCompleted(screen="IN_RUN", duration_ms=9.0, wallet=450)))

    assert state.snapshot()["wallet"] == 450


def test_skips_are_counted_by_reason() -> None:
    state = BotState()
    state.apply(stamped(events.Skipped(action="Damage", reason="unaffordable")))
    state.apply(stamped(events.Skipped(action="Damage", reason="unaffordable")))
    state.apply(stamped(events.Skipped(action="*", reason="screen_gated")))

    assert state.snapshot()["skips"] == {"unaffordable": 2, "screen_gated": 1}


def test_the_snapshot_is_json_serialisable() -> None:
    """It is handed straight to FastAPI; a Counter in it would 500 the route."""
    import json

    state = BotState()
    state.apply(stamped(events.RunStarted(run_id=1)))
    state.apply(stamped(events.Tapped(action="Damage", x=1, y=2, score=0.9)))
    state.apply(stamped(events.BotError(message="device gone")))

    json.dumps(state.snapshot())  # must not raise


def test_the_last_error_is_kept() -> None:
    state = BotState()
    state.apply(stamped(events.BotError(message="device gone")))

    assert state.snapshot()["last_error"] == "device gone"
