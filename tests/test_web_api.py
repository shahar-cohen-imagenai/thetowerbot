from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db
import events
from sinks.sse import SseSink
from sinks.state import BotState
from web.app import create_app


@pytest.fixture
def harness(tmp_path: Path):
    """An app over a real (empty) database, with nothing running behind it."""
    db_path = tmp_path / "bot.db"
    db.connect(db_path).close()
    unknown_dir = tmp_path / "unknown"
    unknown_dir.mkdir()

    state = BotState()
    sse = SseSink(capacity=50)
    bus = events.EventBus()
    bus.subscribe(sse)

    app = create_app(
        state=state, sse=sse, bus=bus, db_path=db_path, unknown_dir=unknown_dir
    )
    return TestClient(app), state, sse, bus, db_path, unknown_dir


def test_status_reports_the_live_state_and_the_dropped_count(harness) -> None:
    client, state, _, bus, _, _ = harness
    state.apply(bus.publish(events.ScanCompleted(screen="IN_RUN", duration_ms=9.0, wallet=450)))
    bus.dropped = 3

    body = client.get("/api/status").json()

    assert body["screen"] == "IN_RUN"
    assert body["wallet"] == 450
    assert body["dropped"] == 3
    assert body["uptime"] >= 0


def test_runs_come_back_newest_first(harness) -> None:
    client, _, _, _, db_path, _ = harness
    conn = db.connect(db_path)
    for run_id in (1, 2):
        db.start_run(conn, run_id, started_at=float(run_id))
    conn.close()

    body = client.get("/api/runs").json()

    assert [run["id"] for run in body] == [2, 1]


def test_the_run_limit_is_clamped_to_something_sane(harness) -> None:
    """?limit=999999 must not try to serialise the whole history."""
    client, *_ = harness

    assert client.get("/api/runs?limit=999999").status_code == 200
    assert client.get("/api/runs?limit=0").status_code == 200


def test_one_runs_events_come_back_with_detail_decoded(harness) -> None:
    client, _, _, _, db_path, _ = harness
    conn = db.connect(db_path)
    db.start_run(conn, 1, started_at=1.0)
    db.insert_event(conn, {
        "seq": 1, "run_id": 1, "ts": 2.0, "type": "Tapped", "screen": "IN_RUN",
        "action": "Damage", "reason": None, "score": 0.9, "price": 10,
        "wallet": 90, "detail": '{"x": 1, "y": 2}',
    })
    conn.close()

    body = client.get("/api/runs/1/events").json()

    assert body[0]["type"] == "Tapped"
    assert body[0]["detail"] == {"x": 1, "y": 2}


def test_unknown_lists_snapshots_newest_first(harness) -> None:
    client, _, _, _, _, unknown_dir = harness
    (unknown_dir / "1000.png").write_bytes(b"one")
    (unknown_dir / "2000.png").write_bytes(b"two")

    body = client.get("/api/unknown").json()

    assert [shot["name"] for shot in body] == ["2000.png", "1000.png"]
    assert body[0]["url"] == "/api/unknown/2000.png"


def test_a_snapshot_is_served_as_a_png(harness) -> None:
    client, _, _, _, _, unknown_dir = harness
    (unknown_dir / "1000.png").write_bytes(b"not really a png")

    response = client.get("/api/unknown/1000.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_a_snapshot_name_cannot_walk_out_of_the_directory(harness) -> None:
    """The name comes off the URL; it must not be able to read the disk."""
    client, *_ = harness

    assert client.get("/api/unknown/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_a_missing_snapshot_directory_is_an_empty_list_not_an_error(tmp_path: Path) -> None:
    """A fresh checkout has never seen an unknown screen."""
    db_path = tmp_path / "bot.db"
    db.connect(db_path).close()
    app = create_app(
        state=BotState(), sse=SseSink(), bus=events.EventBus(),
        db_path=db_path, unknown_dir=tmp_path / "never-created",
    )

    assert TestClient(app).get("/api/unknown").json() == []
