from __future__ import annotations

import sqlite3
from pathlib import Path

import db
import events
from progression import compare_tiers
from sinks.store import StoreSink


def test_existing_database_adds_purpose_and_defaults_legacy_runs_to_farm(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as legacy:
        legacy.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at REAL NOT NULL)")
        legacy.execute("INSERT INTO runs VALUES (1, 100)")
    conn = db.connect(path)
    try:
        assert db.list_runs(conn)[0]["purpose"] == "farm"
        conn.close()
        conn = db.connect(path)
        assert db.list_runs(conn)[0]["purpose"] == "farm"
    finally:
        conn.close()


def test_started_milestone_purpose_survives_run_completion(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "bot.db")
    sink = StoreSink(tmp_path / "bot.db")
    sink._conn = conn
    try:
        sink.handle(events.RunStarted(run_id=1, seq=1, ts=100, purpose="milestone"))
        sink.handle(events.RunEnded(run_id=1, seq=2, ts=200, duration=100, tier=2, coins=1000, wave=100))
        run = db.list_runs(conn)[0]
        assert run["purpose"] == "milestone"
        assert run["ended_at"] == 200
        assert db.run_events(conn, 1)[0]["detail"]["purpose"] == "milestone"
    finally:
        conn.close()


def test_legacy_started_events_and_direct_database_call_default_to_farm(tmp_path: Path) -> None:
    assert events.RunStarted(run_id=1).purpose == "farm"
    conn = db.connect(tmp_path / "bot.db")
    try:
        db.start_run(conn, 1, 100)
        assert db.list_runs(conn)[0]["purpose"] == "farm"
    finally:
        conn.close()


def test_milestone_runs_do_not_bias_farming_recommendations() -> None:
    runs = [dict(tier=1, started_at=0, ended_at=3600, coins=100, wave=100) for _ in range(3)]
    runs += [dict(tier=2, started_at=0, ended_at=1, coins=1000, wave=200, purpose="milestone") for _ in range(3)]
    comparison = compare_tiers(runs)
    assert comparison["recommended_tier"] == 1
    assert [tier["tier"] for tier in comparison["tiers"]] == [1]
    assert comparison["tiers"][0]["coins_per_hour"] == 100


def test_only_milestone_runs_cannot_establish_a_farming_baseline() -> None:
    runs = [dict(tier=2, started_at=0, ended_at=60, coins=1000, purpose="milestone") for _ in range(3)]
    assert compare_tiers(runs)["recommended_tier"] is None
