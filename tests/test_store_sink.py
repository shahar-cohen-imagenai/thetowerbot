from __future__ import annotations

from pathlib import Path

import db
import events
from sinks.store import StoreSink, to_row


def stamped(event: events.Event, seq: int = 1, ts: float = 1000.0) -> events.Event:
    import dataclasses

    return dataclasses.replace(event, seq=seq, ts=ts)


def drain(tmp_path: Path, stream: list[events.Event]) -> Path:
    """Feed a sink and shut it down, so every event is written before we read."""
    path = tmp_path / "bot.db"
    sink = StoreSink(path)
    sink.start()
    bus = events.EventBus()
    bus.subscribe(sink)
    for event in stream:
        bus.publish(event)
    sink.close()
    return path


def test_a_tap_maps_its_typed_columns_and_blobs_the_rest() -> None:
    row = to_row(stamped(events.Tapped(action="Damage", x=840, y=1520, score=0.94,
                                       price=120, wallet=300)), run_id=2)

    assert (row["type"], row["action"], row["run_id"]) == ("Tapped", "Damage", 2)
    assert (row["score"], row["price"], row["wallet"]) == (0.94, 120, 300)
    assert '"x": 840' in row["detail"] and '"y": 1520' in row["detail"]


def test_a_screen_change_stores_the_new_screen_in_the_screen_column() -> None:
    """`curr` IS the screen; blobbing it would leave the column empty on the
    one event that defines it."""
    row = to_row(
        stamped(events.ScreenChanged(prev="MAIN_MENU", curr="IN_RUN",
                                     confidence=0.99, scores={"IN_RUN": 0.99})),
        run_id=None,
    )

    assert row["screen"] == "IN_RUN"
    assert '"prev": "MAIN_MENU"' in row["detail"]


def test_scan_completed_is_never_stored(tmp_path: Path) -> None:
    """43,000 near-identical rows a day is not history, it is noise."""
    path = drain(tmp_path, [
        events.RunStarted(run_id=1),
        events.ScanCompleted(screen="IN_RUN", duration_ms=90.0, wallet=300),
        events.ScanCompleted(screen="IN_RUN", duration_ms=91.0, wallet=310),
    ])

    with db.reader(path) as conn:
        stored = db.run_events(conn, 1)

    assert [e["type"] for e in stored] == ["RunStarted"]


def test_a_screen_change_that_changed_nothing_is_not_stored(tmp_path: Path) -> None:
    """Booting onto an unmodelled screen emits UNKNOWN -> UNKNOWN once."""
    path = drain(tmp_path, [
        events.RunStarted(run_id=1),
        events.ScreenChanged(prev="UNKNOWN", curr="UNKNOWN", confidence=0.4, scores={}),
    ])

    with db.reader(path) as conn:
        stored = db.run_events(conn, 1)

    assert [e["type"] for e in stored] == ["RunStarted"]


def test_events_are_correlated_to_the_open_run(tmp_path: Path) -> None:
    path = drain(tmp_path, [
        events.RunStarted(run_id=1),
        events.Tapped(action="Damage", x=1, y=2, score=0.9),
        events.RunEnded(run_id=1, duration=60.0, wave=12, coins=900, tier=2),
        events.Navigated(target="RETRY"),
    ])

    with db.reader(path) as conn:
        in_run = db.run_events(conn, 1)
        orphans = conn.execute(
            "SELECT type FROM events WHERE run_id IS NULL"
        ).fetchall()

    assert [e["type"] for e in in_run] == ["RunStarted", "Tapped", "RunEnded"]
    assert [row["type"] for row in orphans] == ["Navigated"]


def test_the_run_row_carries_its_stats_and_counters(tmp_path: Path) -> None:
    path = drain(tmp_path, [
        events.RunStarted(run_id=1),
        events.ScanCompleted(screen="IN_RUN", duration_ms=90.0),
        events.ScanCompleted(screen="IN_RUN", duration_ms=90.0),
        events.Tapped(action="Damage", x=1, y=2, score=0.9),
        events.RunEnded(run_id=1, duration=60.0, wave=12, coins=900, tier=2),
    ])

    with db.reader(path) as conn:
        run = db.list_runs(conn)[0]

    assert (run["wave"], run["coins"], run["tier"]) == (12, 900, 2)
    assert (run["scan_count"], run["tap_count"]) == (2, 1)
    assert run["ended_at"] is not None


def test_counters_reset_between_runs(tmp_path: Path) -> None:
    path = drain(tmp_path, [
        events.RunStarted(run_id=1),
        events.Tapped(action="Damage", x=1, y=2, score=0.9),
        events.RunEnded(run_id=1, duration=10.0),
        events.RunStarted(run_id=2),
        events.RunEnded(run_id=2, duration=10.0),
    ])

    with db.reader(path) as conn:
        runs = {r["id"]: r for r in db.list_runs(conn)}

    assert runs[1]["tap_count"] == 1
    assert runs[2]["tap_count"] == 0
