"""The dashboard's HTTP layer: JSON for the machine, a built SPA for the eye.

Two sources, deliberately separate. "What is the bot doing right now" is a
memory question, answered from the shared BotState. "What did it do" is a
history question, answered by read-only SQLite connections opened per request
- the web layer never writes, and cannot: db.reader() opens mode=ro.

Binds 127.0.0.1. No auth, /api/unknown serves screenshots of a live session,
and /api/control lets a caller pause, reconfigure or stop the bot - see the
warning beside config.WEB_HOST before changing the bind address.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import db
import events
from control import ControlError, Controls
from events import EventBus
from frames import FrameBuffer
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
    stop: threading.Event | None = None,
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

    `stop` is the other way this can end, and the one that matters at
    shutdown: `is_disconnected()` only reports true once the browser closes
    the tab, which it never does on its own just because the bot stopped.
    Without `stop`, a held-open dashboard tab and a uvicorn server with
    `should_exit = True` wait on each other forever - the response is still
    "in flight" as far as the server's graceful shutdown is concerned, so
    the transport never closes. Checking the flag lets the generator end
    itself, the response complete, and the connection close normally.
    Optional so existing callers (and every test predating this) keep
    working; a fresh Event() that nobody ever sets is exactly "never stop
    this way", which is the old behaviour.
    """
    if stop is None:
        stop = threading.Event()

    latest = sse.latest_seq()
    if cursor > latest:
        # A Last-Event-ID from before a seq reset (--no-store, or a fresh
        # --db swap): since() filters on seq > cursor, so a stale cursor
        # higher than anything the ring holds would starve the feed until
        # enough new events accumulate to pass it - the tab just looks dead.
        # Replay everything currently buffered instead of nothing.
        cursor = 0

    idle = 0.0
    while not stop.is_set() and not await is_disconnected():
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


BOUNDARY = "frame"


async def frame_stream(
    frames: FrameBuffer,
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    stop: threading.Event,
    poll: float = 0.25,
) -> AsyncIterator[bytes]:
    """MJPEG: one connection, rendered natively by a plain <img>.

    Same two exits as event_stream(), and for the same reasons: the browser
    closing the tab, and the bot shutting down. An <img> holds its response
    open indefinitely and never disconnects on its own, so without the `stop`
    check a held-open device view and a shutting-down uvicorn would wait on
    each other forever.

    Only sends when the frame number moves, so an idle bot costs one send per
    scan rather than one per poll.
    """
    sent = 0
    while not stop.is_set() and not await is_disconnected():
        current = frames.latest()
        if current is not None and current[0] != sent:
            sent, payload = current
            yield (
                f"--{BOUNDARY}\r\n"
                f"Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(payload)}\r\n\r\n"
            ).encode("ascii") + payload + b"\r\n"
        await asyncio.sleep(poll)


class ControlPatch(BaseModel):
    """A partial update. Every field optional; absent means "leave it alone".

    Deliberately loose on types beyond the obvious - Controls.apply() is the
    single validator, so the rules live in one place rather than being spelled
    out here and there and drifting apart.
    """

    paused: bool | None = None
    interval: float | None = None
    auto_navigate: bool | None = None
    strategy: str | None = None
    enabled_actions: list[str] | None = None


def create_app(
    *,
    state: BotState,
    sse: SseSink,
    bus: EventBus,
    db_path: Path | None = config.DB_PATH,
    unknown_dir: Path = config.UNKNOWN_DIR,
    stop: threading.Event | None = None,
    controls: Controls | None = None,
    checks: Mapping[str, Any] | None = None,
    frames: FrameBuffer | None = None,
) -> FastAPI:
    # See event_stream()'s docstring for why this exists: without it, an
    # open dashboard tab and a shutting-down uvicorn wait on each other
    # forever. Optional, and created fresh here rather than defaulted on the
    # function signature, so a caller that never passes one (every test
    # predating this finding) keeps the old "never stops this way" behaviour
    # without every call site sharing one mutable Event by accident.
    if stop is None:
        stop = threading.Event()

    app = FastAPI(title="The Tower bot")

    @app.get("/api/status")
    def status() -> dict:
        payload = state.snapshot()
        # Dropped events are the bus's business, not the state's: they were
        # never delivered to a sink, so no accumulator ever saw them.
        payload["dropped"] = bus.dropped
        # Overlay geometry rides along with the status poll rather than getting
        # its own endpoint: it changes exactly as often as the status does, and
        # a second poll would buy nothing.
        payload["boxes"] = frames.boxes() if frames is not None else []
        payload["frame_size"] = None
        if frames is not None:
            size = frames.size()
            if size is not None:
                payload["frame_size"] = {"width": size[0], "height": size[1]}
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
            event_stream(
                sse, resume_point(request), request.is_disconnected, stop=stop
            ),
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

    @app.get("/api/frame.jpg")
    def frame_still() -> Response:
        current = frames.latest() if frames is not None else None
        if current is None:
            raise HTTPException(status_code=404, detail="no frame captured yet")
        return Response(content=current[1], media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/frame")
    async def frame_mjpeg(request: Request) -> StreamingResponse:
        if frames is None:
            raise HTTPException(status_code=404, detail="no frame buffer")
        return StreamingResponse(
            frame_stream(frames, request.is_disconnected, stop=stop),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    if controls is not None:
        available = dict(checks or {})

        def _control_payload() -> dict:
            # The browser needs the full action list to render checkboxes for
            # the ones currently switched off, which the snapshot omits, plus
            # which strategies actually built (see build_affordability()) so
            # it can grey out one with no atlas rather than let a switch to it
            # silently do nothing.
            payload = controls.snapshot()
            payload["actions"] = [action.name for action in config.ACTIONS]
            payload["strategies_available"] = sorted(
                name for name, check in available.items() if check is not None
            )
            return payload

        @app.get("/api/control")
        def read_control() -> dict:
            return _control_payload()

        @app.patch("/api/control")
        def patch_control(patch: ControlPatch) -> dict:
            requested = patch.model_dump(exclude_none=True)

            # Refuse before applying, not after: build_affordability() falls
            # back to brightness on its own, so accepting this and letting the
            # loop pick would leave the browser showing "digits" while the bot
            # used brightness.
            strategy = requested.get("strategy")
            if strategy is not None and available.get(strategy) is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"strategy {strategy!r} is unavailable - no glyph atlas is built",
                )

            try:
                changed = controls.apply(requested)
            except ControlError as exc:
                raise HTTPException(status_code=422, detail=f"{exc.field}: {exc}") from exc

            if changed:
                # Only when something actually moved. A no-op patch is not a
                # state change and must not fill the log with noise.
                bus.publish(events.ControlChanged(changed=changed, source="web"))

            return _control_payload()

        @app.post("/api/control/stop")
        def stop_bot() -> dict:
            # This route has no handle on the worker thread or the Server -
            # the shared `stop` Event is the only thing it can reach from a
            # request handler. serve_web()'s stop-watch thread is the one
            # actually waiting on it: it calls bot.stop() and sets
            # server.should_exit, which is what brings the loop and the
            # server down together.
            stop.set()
            return {"stopping": True}

    @app.get("/api/stats")
    def stats() -> dict:
        # --no-store is a supported mode, not an error: empty aggregates are
        # the honest answer, and the page renders an explicit empty state.
        if db_path is None:
            return {"runs": [], "taps": [], "screens": []}
        with db.reader(db_path) as conn:
            return {
                "runs": db.run_stats(conn),
                "taps": db.taps_by_action(conn),
                "screens": db.screen_histogram(conn),
            }

    @app.get("/api/errors")
    def errors(limit: int = 100) -> list[dict]:
        if db_path is None:
            return []
        with db.reader(db_path) as conn:
            return db.error_log(conn, limit=max(1, min(limit, 500)))

    # Last, deliberately. Starlette matches routes in registration order and a
    # mount at "/" matches everything, so every /api route above must already
    # be registered or the mount would swallow the whole API.
    #
    # html=True resolves "/runs/" to "runs/index.html", which is the layout
    # next.config.ts's trailingSlash:true produces.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    else:
        @app.get("/", response_class=HTMLResponse)
        def not_built() -> str:
            # Only reachable in a working tree whose build was deleted; the
            # committed web/static/ means a fresh clone never sees this.
            return (
                "<h1>Dashboard not built</h1>"
                "<p>Run <code>npm run build</code> in <code>web/ui</code>.</p>"
            )

    return app
