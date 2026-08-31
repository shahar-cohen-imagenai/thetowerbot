from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

import db


def make_db(tmp_path: Path) -> sqlite3.Connection:
    return db.connect(tmp_path / "bot.db")


def a_row(seq: int, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "seq": seq,
        "run_id": 1,
        "ts": 1000.0 + seq,
        "type": "Tapped",
        "screen": "IN_RUN",
        "action": "Damage",
        "reason": None,
        "score": 0.94,
        "price": 120,
        "wallet": 300,
        "detail": '{"x": 840, "y": 1520}',
    }
    row.update(overrides)
    return row


def test_an_inserted_event_reads_back_with_its_detail_decoded(tmp_path: Path) -> None:
    conn = make_db(tmp_path)
    db.start_run(conn, 1, started_at=999.0)
    db.insert_event(conn, a_row(1))

    stored = db.run_events(conn, 1)

    assert len(stored) == 1
    assert stored[0]["action"] == "Damage"
    assert stored[0]["detail"] == {"x": 840, "y": 1520}


def test_seq_seeds_from_the_stored_maximum(tmp_path: Path) -> None:
    """A restart must not reset the counter onto rows that already exist."""
    conn = make_db(tmp_path)
    assert db.max_seq(conn) == 0

    db.insert_event(conn, a_row(7))
    assert db.max_seq(conn) == 7


def test_run_ids_seed_from_the_stored_maximum(tmp_path: Path) -> None:
    conn = make_db(tmp_path)
    assert db.max_run_id(conn) == 0

    db.start_run(conn, 4, started_at=1.0)
    assert db.max_run_id(conn) == 4


def test_finishing_a_run_keeps_the_start_it_was_opened_with(tmp_path: Path) -> None:
    conn = make_db(tmp_path)
    db.start_run(conn, 1, started_at=100.0)

    db.finish_run(
        conn, 1, started_at=0.0, ended_at=250.0, wave=137, coins=4200,
        tier=3, abandoned=False, scan_count=61, tap_count=12,
    )

    run = db.list_runs(conn)[0]
    assert run["started_at"] == 100.0
    assert (run["ended_at"], run["wave"], run["coins"], run["tier"]) == (250.0, 137, 4200, 3)
    assert (run["scan_count"], run["tap_count"], run["abandoned"]) == (61, 12, 0)


def test_finishing_an_unseen_run_still_records_it(tmp_path: Path) -> None:
    """A run opened before a crash must not vanish because its row is missing."""
    conn = make_db(tmp_path)

    db.finish_run(
        conn, 9, started_at=100.0, ended_at=160.0, wave=None, coins=None,
        tier=None, abandoned=True, scan_count=3, tap_count=0,
    )

    run = db.list_runs(conn)[0]
    assert run["id"] == 9
    assert run["started_at"] == 100.0
    assert run["abandoned"] == 1


def test_runs_come_back_newest_first_and_honour_the_limit(tmp_path: Path) -> None:
    conn = make_db(tmp_path)
    for run_id in (1, 2, 3):
        db.start_run(conn, run_id, started_at=float(run_id))

    assert [r["id"] for r in db.list_runs(conn, limit=2)] == [3, 2]


def test_pruning_deletes_old_events_and_keeps_recent_ones(tmp_path: Path) -> None:
    conn = make_db(tmp_path)
    now = time.time()
    db.insert_event(conn, a_row(1, ts=now - 40 * 86400))
    db.insert_event(conn, a_row(2, ts=now - 1 * 86400))

    removed = db.prune_events(conn, retention_days=30, now=now)

    assert removed == 1
    assert [e["seq"] for e in db.run_events(conn, 1)] == [2]


def test_a_reader_connection_cannot_write(tmp_path: Path) -> None:
    """The web layer opens these; a bug there must not corrupt the log."""
    path = tmp_path / "bot.db"
    db.connect(path).close()

    with db.reader(path) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM events")
