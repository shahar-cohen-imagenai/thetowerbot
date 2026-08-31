from __future__ import annotations

import asyncio
import itertools
import json
import threading
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

import pytest
from fastapi.testclient import TestClient

import db
import events
from sinks.sse import SseSink
from sinks.state import BotState
from web.app import create_app, event_stream, resume_point


@pytest.fixture
def harness(tmp_path: Path) -> tuple[TestClient, BotState, SseSink, events.EventBus, Path, Path]:
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
    """?limit=999999 must not try to serialise the whole history, and
    ?limit=0 must not come back empty - both asserted on the actual body,
    not just a 200, which would still pass with max(1, ...) deleted."""
    client, _, _, _, db_path, _ = harness
    conn = db.connect(db_path)
    for run_id in (1, 2, 3):
        db.start_run(conn, run_id, started_at=float(run_id))
    conn.close()

    assert client.get("/api/runs?limit=999999").status_code == 200

    body = client.get("/api/runs?limit=0").json()
    assert len(body) == 1


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


def test_an_encoded_slash_never_reaches_the_snapshot_route(harness) -> None:
    """A percent-encoded slash is rejected by Starlette's routing itself, before
    the handler runs - so this only proves the router's own behaviour. The
    guard's real protective value (a name that stays inside one path segment
    but still escapes the directory, e.g. via a symlink) is exercised by
    test_a_symlink_escaping_the_directory_is_refused below.
    """
    client, *_ = harness

    assert client.get("/api/unknown/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_a_symlink_escaping_the_directory_is_refused(harness, tmp_path: Path) -> None:
    """A name with no slash in it at all - passes the router - can still name a
    symlink that resolves outside unknown_dir. resolve() must catch that."""
    client, _, _, _, _, unknown_dir = harness
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"outside the sandbox")
    (unknown_dir / "escape.png").symlink_to(secret)

    assert client.get("/api/unknown/escape.png").status_code == 404


def test_a_missing_snapshot_directory_is_an_empty_list_not_an_error(tmp_path: Path) -> None:
    """A fresh checkout has never seen an unknown screen."""
    db_path = tmp_path / "bot.db"
    db.connect(db_path).close()
    app = create_app(
        state=BotState(), sse=SseSink(), bus=events.EventBus(),
        db_path=db_path, unknown_dir=tmp_path / "never-created",
    )

    assert TestClient(app).get("/api/unknown").json() == []


# --- /api/events/stream -----------------------------------------------------
#
# event_stream() is driven directly here instead of through TestClient.stream():
# Starlette's TestClient buffers an entire ASGI response before returning
# anything to the caller (portal.call(self.app, ...) in its testclient runs
# the app coroutine to completion first), so a generator that only ends on
# disconnect can never be observed mid-stream through it - the `with
# client.stream(...)` call itself just hangs forever, no matter what the test
# body does afterwards. Calling event_stream() as a plain async generator
# sidesteps that entirely and cannot hang: `_disconnect_after` guarantees it
# terminates within a bounded number of polls.


def _disconnect_after(n: int) -> Callable[[], Awaitable[bool]]:
    """A fake `is_disconnected`: reports connected for the first `n` polls,
    then disconnected - so event_stream() drains what it should and then
    terminates, instead of the infinite loop it runs in production."""
    calls = itertools.count()

    async def is_disconnected() -> bool:
        return next(calls) >= n

    return is_disconnected


def _drain(gen: AsyncIterator[str]) -> list[str]:
    """Run an event_stream() generator to completion and return the SSE
    lines it produced, blank lines dropped - the same shape iter_lines()
    would hand a real HTTP client, since each yield is one `id:`/`data:`
    pair joined by embedded newlines rather than two separate messages."""

    async def collect() -> list[str]:
        lines: list[str] = []
        async for chunk in gen:
            lines.extend(line for line in chunk.splitlines() if line)
        return lines

    return asyncio.run(collect())


def test_the_stream_replays_what_is_buffered_to_a_fresh_client() -> None:
    """A pre-filled ring, drained from cursor 0, replays oldest first - and
    carries no `event:` field, since the type lives in the JSON body."""
    sse = SseSink(capacity=50)
    sse.offer(events.RunStarted(run_id=1, seq=1))
    sse.offer(events.Tapped(action="Damage", x=1, y=2, score=0.9, seq=2))

    lines = _drain(event_stream(sse, 0, _disconnect_after(1), poll=0.001, heartbeat=100.0))

    assert not any(line.startswith("event:") for line in lines)
    ids = [line[len("id:"):].strip() for line in lines if line.startswith("id:")]
    payloads = [json.loads(line[len("data:"):]) for line in lines if line.startswith("data:")]
    assert ids == ["1", "2"]
    assert [payload["type"] for payload in payloads] == ["RunStarted", "Tapped"]
    assert payloads[1]["action"] == "Damage"


def test_a_reconnecting_browser_resumes_from_its_last_event_id() -> None:
    """This is what seq is for: no gap and no duplicate across a reconnect."""
    sse = SseSink(capacity=50)
    for seq in (1, 2, 3):
        sse.offer(events.Navigated(target="RETRY", seq=seq))
    sse.offer(events.RunStarted(run_id=7, seq=4))

    lines = _drain(event_stream(sse, 3, _disconnect_after(1), poll=0.001, heartbeat=100.0))

    payloads = [json.loads(line[len("data:"):]) for line in lines if line.startswith("data:")]
    assert [payload["type"] for payload in payloads] == ["RunStarted"]
    assert payloads[0]["seq"] == 4


def test_resume_point_prefers_last_event_id_then_since_then_zero() -> None:
    """A tiny stub stands in for Request: resume_point only ever touches
    .headers and .query_params, and there is no live ASGI scope here to
    build a real Request from."""

    class _RequestStub:
        def __init__(self, headers: dict[str, str], params: dict[str, str]) -> None:
            self.headers = headers
            self.query_params = params

    assert resume_point(_RequestStub({"last-event-id": "7"}, {})) == 7
    assert resume_point(_RequestStub({}, {"since": "5"})) == 5
    assert resume_point(_RequestStub({"last-event-id": "not-a-number"}, {})) == 0
    assert resume_point(_RequestStub({}, {})) == 0


def test_an_idle_stream_sends_a_heartbeat_ping() -> None:
    sse = SseSink(capacity=50)

    lines = _drain(event_stream(sse, 0, _disconnect_after(1), poll=0.001, heartbeat=0.001))

    assert lines == [": ping"]


def test_the_generator_stops_once_the_client_has_disconnected() -> None:
    """Once is_disconnected reports True the generator ends instead of
    spinning - this is what keeps every other test above from hanging."""
    sse = SseSink(capacity=50)
    gen = event_stream(sse, 0, _disconnect_after(0), poll=0.001, heartbeat=100.0)

    async def step() -> None:
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(step())


async def _is_never_disconnected() -> bool:
    return False


def test_an_already_set_stop_flag_ends_the_stream_immediately() -> None:
    """Finding 1: the shutdown deadlock happens because is_disconnected()
    never fires while a tab is open. `stop` has to be able to end the
    generator on its own, with is_disconnected() stuck reporting False."""
    sse = SseSink(capacity=50)
    stop = threading.Event()
    stop.set()
    gen = event_stream(sse, 0, _is_never_disconnected, poll=0.001, stop=stop)

    async def step() -> None:
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(step())


def test_stop_set_mid_stream_ends_it_on_the_next_poll() -> None:
    """The actual shutdown scenario: the flag flips after the generator has
    already yielded at least one frame, with the browser still connected.
    A generator that only checked `stop` before its first yield would pass
    the test above and still hang here."""
    sse = SseSink(capacity=50)
    sse.offer(events.RunStarted(run_id=1, seq=1))
    stop = threading.Event()
    gen = event_stream(sse, 0, _is_never_disconnected, poll=0.001, stop=stop)

    async def step() -> None:
        first = await gen.__anext__()
        assert "RunStarted" in first
        stop.set()
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(step())


def test_a_stale_last_event_id_past_the_ring_is_reset_to_replay_everything() -> None:
    """M3: --no-store (or a fresh --db) restarts seq at 1 while an already
    open tab still sends its old, now-unreachably-high Last-Event-ID. Without
    a reset, since() filters on seq > cursor forever and the feed looks dead
    until enough new events accumulate to pass the stale cursor."""
    sse = SseSink(capacity=50)
    sse.offer(events.RunStarted(run_id=1, seq=1))
    sse.offer(events.Tapped(action="Damage", x=1, y=2, score=0.9, seq=2))

    stale_cursor = 900  # higher than anything the ring holds
    lines = _drain(
        event_stream(sse, stale_cursor, _disconnect_after(1), poll=0.001, heartbeat=100.0)
    )

    payloads = [json.loads(line[len("data:"):]) for line in lines if line.startswith("data:")]
    assert [payload["type"] for payload in payloads] == ["RunStarted", "Tapped"]


def test_the_dashboard_is_served_at_the_root(harness) -> None:
    client, *_ = harness

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "EventSource" in response.text  # it is the live page, not a stub


def test_run_routes_degrade_to_empty_history_under_no_store() -> None:
    """--no-store is a supported mode, not an error: there is no database
    file, so an empty history is the honest answer, not a 500."""
    app = create_app(
        state=BotState(), sse=SseSink(), bus=events.EventBus(), db_path=None,
    )
    client = TestClient(app)

    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/runs").json() == []
    assert client.get("/api/runs/1/events").status_code == 200
    assert client.get("/api/runs/1/events").json() == []
