"""The dashboard's HTTP layer: JSON for the machine, one HTML file for the eye.

Two sources, deliberately separate. "What is the bot doing right now" is a
memory question, answered from the shared BotState. "What did it do" is a
history question, answered by read-only SQLite connections opened per request
- the web layer never writes, and cannot: db.reader() opens mode=ro.

Binds 127.0.0.1. No auth, and /api/unknown serves screenshots of a live
session - see the warning beside config.WEB_HOST before changing that.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

import config
import db
from events import EventBus
from sinks.sse import SseSink, to_payload
from sinks.state import BotState

STATIC_DIR = Path(__file__).parent / "static"

# One page of history is plenty for a dashboard, and it bounds the response
# whatever the query string asks for.
MAX_RUNS_PER_PAGE = 500


def resume_point(request: Request) -> int:
    """Where to start streaming from.

    A reconnecting browser sends Last-Event-ID and gets exactly what it
    missed. Anything else - a first visit, a hand-rolled curl, a garbled
    header - starts from 0 and gets the whole ring, so the feed is never
    blank on arrival.
    """
    raw = request.headers.get("last-event-id") or request.query_params.get("since")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


async def event_stream(
    sse: SseSink,
    cursor: int,
    is_disconnected: Callable[[], Awaitable[bool]],
    poll: float = config.SSE_POLL_SECONDS,
    heartbeat: float = config.SSE_HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    """Server-sent events, resumable through Last-Event-ID.

    Polling the ring rather than being pushed to: events arrive on the
    scan loop's thread and are consumed by the event loop, and a poll at
    four times a second against a 2s scan interval is imperceptible while
    needing no cross-thread wakeup at all.

    Kept as a standalone generator - rather than inlined in the route -
    so it can be driven directly in tests. FastAPI's TestClient buffers an
    entire ASGI response before handing anything back to the caller, so a
    generator whose whole point is to keep going until the browser
    disconnects can never be observed mid-stream that way; it has to be
    iterated on its own instead. `is_disconnected` is injected for exactly
    that reason too - production passes `request.is_disconnected`, tests
    pass a fake that reports disconnection after a bounded number of polls.
    """
    idle = 0.0
    while not await is_disconnected():
        batch = sse.since(cursor)
        for event in batch:
            cursor = event.seq
            yield (
                f"id: {event.seq}\n"
                f"data: {json.dumps(to_payload(event))}\n\n"
            )
        idle = 0.0 if batch else idle + poll
        if idle >= heartbeat:
            idle = 0.0
            yield ": ping\n\n"  # keeps an idle connection alive
        await asyncio.sleep(poll)


def create_app(
    *,
    state: BotState,
    sse: SseSink,
    bus: EventBus,
    db_path: Path | None = config.DB_PATH,
    unknown_dir: Path = config.UNKNOWN_DIR,
) -> FastAPI:
    app = FastAPI(title="The Tower bot")

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        # Read per request rather than cached at import: editing the page and
        # hitting refresh is the whole development loop for it.
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/status")
    def status() -> dict:
        payload = state.snapshot()
        # Dropped events are the bus's business, not the state's: they were
        # never delivered to a sink, so no accumulator ever saw them.
        payload["dropped"] = bus.dropped
        return payload

    @app.get("/api/runs")
    def runs(limit: int = 50) -> list[dict]:
        # --no-store is a supported mode, not an error: there is no file to
        # read, so an empty history is the honest answer, not a 500.
        if db_path is None:
            return []
        with db.reader(db_path) as conn:
            return db.list_runs(conn, limit=max(1, min(limit, MAX_RUNS_PER_PAGE)))

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: int) -> list[dict]:
        # Same as /api/runs above: --no-store means there is nothing to read.
        if db_path is None:
            return []
        with db.reader(db_path) as conn:
            return db.run_events(conn, run_id)

    @app.get("/api/events/stream")
    async def stream(request: Request) -> StreamingResponse:
        return StreamingResponse(
            event_stream(sse, resume_point(request), request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/unknown")
    def unknown() -> list[dict]:
        if not unknown_dir.exists():
            return []
        shots = sorted(
            unknown_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        return [
            {"name": p.name, "ts": p.stat().st_mtime, "url": f"/api/unknown/{p.name}"}
            for p in shots
        ]

    @app.get("/api/unknown/{name}")
    def unknown_image(name: str) -> FileResponse:
        # The name arrives off the URL, percent-decoded. Resolve it and check
        # the parent rather than trusting it: "../../etc/passwd" must 404, not
        # read the disk.
        path = (unknown_dir / name).resolve()
        if path.parent != unknown_dir.resolve() or not path.is_file():
            raise HTTPException(status_code=404, detail="no such snapshot")
        return FileResponse(path, media_type="image/png")

    return app
