"""The dashboard's HTTP layer: JSON for the machine, a built SPA for the eye.

Two sources, deliberately separate. "What is the bot doing right now" is a
memory question, answered from the shared BotState. "What did it do" is a
history question, answered by read-only SQLite connections opened per request.

Binds 127.0.0.1. No auth, /api/unknown serves screenshots of a live session,
and the control plane lets a caller start and stop the bot, rewrite what it
buys, and create or delete strategy files on disk - see the warning beside
config.WEB_HOST before changing the bind address.

"The web layer never writes" is no longer true in general, and the narrower
claim is the one that matters: it never writes to the DATABASE. db.reader()
opens mode=ro and nothing here can change that. Strategy files are the one
thing it does write, through StrategyStore, which validates every profile
before it reaches the disk.
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
from runner import BotRunner, RunnerError
from sinks.sse import SseSink, to_payload
from sinks.state import BotState
from strategy import Strategy, StrategyStore

STATIC_DIR = Path(__file__).parent / "static"

# One page of history is plenty for a dashboard, and it bounds the response
# whatever the query string asks for.
MAX_RUNS_PER_PAGE = 500

# strategy.ControlError carries a `code` naming the KIND of failure, so the
# routes below map a status without matching on message text.
_STATUS_FOR_CODE = {"not_found": 404, "conflict": 409, "invalid": 422}


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
    process shutdown: `is_disconnected()` only reports true once the browser
    closes the tab, which it never does on its own just because the process
    is going down. Without `stop`, a held-open dashboard tab and a uvicorn
    server with `should_exit = True` wait on each other forever - the
    response is still "in flight" as far as the server's graceful shutdown
    is concerned, so the transport never closes. Checking the flag lets the
    generator end itself, the response complete, and the connection close
    normally. Optional so existing callers (and every test predating this)
    keep working; a fresh Event() that nobody ever sets is exactly "never
    stop this way", which is the old behaviour.
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
    poll: float = config.FRAME_POLL_SECONDS,
) -> AsyncIterator[bytes]:
    """MJPEG: one connection, rendered natively by a plain <img>.

    Same two exits as event_stream(), and for the same reasons: the browser
    closing the tab, and the process shutting down. An <img> holds its
    response open indefinitely and never disconnects on its own, so without
    the `stop` check a held-open device view and a shutting-down uvicorn
    would wait on each other forever.

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
    out here and there and drifting apart. `actions` is a list of raw objects
    for the same reason: mirroring ActionRule's fields here would be a second
    schema to keep in step with strategy.py.
    """

    paused: bool | None = None
    affordability: str | None = None
    interval: float | None = None
    click_cooldown: float | None = None
    auto_navigate: bool | None = None
    max_runs: int | None = None
    navigation_cooldown: float | None = None
    screen_confirmations: int | None = None
    actions: list[dict[str, Any]] | None = None


def create_app(
    *,
    state: BotState,
    sse: SseSink,
    bus: EventBus,
    db_path: Path | None = config.DB_PATH,
    unknown_dir: Path = config.UNKNOWN_DIR,
    shutdown: threading.Event | None = None,
    controls: Controls | None = None,
    checks: Mapping[str, Any] | Callable[[], Mapping[str, Any]] | None = None,
    frames: FrameBuffer | None = None,
    runner: BotRunner | None = None,
    store: StrategyStore | None = None,
) -> FastAPI:
    # See event_stream()'s docstring for why this exists: without it, an
    # open dashboard tab and a shutting-down uvicorn wait on each other
    # forever. Optional, and created fresh here rather than defaulted on the
    # function signature, so a caller that never passes one (every test
    # predating this finding) keeps the old "never stops this way" behaviour
    # without every call site sharing one mutable Event by accident.
    #
    # This is the PROCESS going down, not the bot - see runner.BotRunner for
    # that half. A dashboard can now outlive the bot it was watching, so the
    # two can no longer share one flag: stopping the bot must never trip
    # this one, and this one is not the runner's business at all.
    if shutdown is None:
        shutdown = threading.Event()

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
        # Always present, even with no runner (--once, --tui, and every test
        # predating the lifecycle split), so the browser needs no special case
        # for "this build cannot start a bot".
        payload["bot"] = (
            runner.status()
            if runner is not None
            else {"running": False, "since": None, "error": None}
        )
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
                sse, resume_point(request), request.is_disconnected, stop=shutdown
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
        # Same distinction frame_mjpeg already makes: no buffer at all (this
        # process was never given one - --once, --tui, or plain logging) is
        # a different fact than a buffer that simply has not been fed a
        # frame yet, and deserves its own message rather than one that
        # implies a frame is merely still on its way.
        if frames is None:
            raise HTTPException(status_code=404, detail="no frame buffer")
        current = frames.latest()
        if current is None:
            raise HTTPException(status_code=404, detail="no frame captured yet")
        return Response(content=current[1], media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/frame")
    async def frame_mjpeg(request: Request) -> StreamingResponse:
        if frames is None:
            raise HTTPException(status_code=404, detail="no frame buffer")
        return StreamingResponse(
            frame_stream(frames, request.is_disconnected, stop=shutdown),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    if controls is not None:

        def _available_checks() -> Mapping[str, Any]:
            # Resolved on every call, not snapshotted once at create_app
            # time: under --idle there is no bot (and so no checks) when
            # create_app runs, so a dict captured here would freeze at {}
            # forever and refuse every affordability PATCH with "no glyph
            # atlas is built", even after Start later builds real ones.
            # `checks` may be a plain dict (every caller today) or a
            # zero-arg callable that hands back whatever the runner
            # currently holds - either way this reads it fresh.
            return checks() if callable(checks) else (checks or {})

        def _control_payload() -> dict:
            # The browser needs to know which affordability methods actually
            # built (see build_affordability()) so it can grey out one with no
            # atlas rather than let a switch to it silently do nothing. The
            # action list needs no separate advertisement any more: the
            # strategy carries every row, enabled or not.
            payload = controls.payload()
            payload["affordability_available"] = sorted(
                name for name, check in _available_checks().items()
                if check is not None
            )
            return payload

        @app.get("/api/control")
        def read_control() -> dict:
            return _control_payload()

        @app.patch("/api/control")
        def patch_control(patch: ControlPatch) -> dict:
            # exclude_none means "absent" and "explicitly null" are the same
            # request, so max_runs cannot be cleared through this route. The
            # strategy page clears it by PUTting the whole profile instead.
            requested = patch.model_dump(exclude_none=True)

            # Refuse before applying, not after: build_affordability() falls
            # back to brightness on its own, so accepting this and letting the
            # loop pick would leave the browser showing "digits" while the bot
            # used brightness.
            affordability = requested.get("affordability")
            if affordability is not None and _available_checks().get(affordability) is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"affordability {affordability!r} is unavailable - no glyph "
                        "atlas is built"
                    ),
                )

            try:
                if "actions" in requested:
                    # Controls.apply() validates everything a Strategy can
                    # know about itself, which does not include whether a
                    # template is a real file inside TEMPLATE_DIR - that is
                    # disk I/O, and control.py is imported by the scan loop
                    # and must stay I/O-free. So the filesystem half runs
                    # here. Without it a request body chooses which file
                    # cv2.imread opens, and an unresolvable template raises
                    # out of every single scan pass, forever, instead of
                    # once as the 422 below.
                    #
                    # Yes, this merges twice - once to validate, once inside
                    # apply(). merged() is pure and this runs once per
                    # request rather than once per scan, so the duplicate
                    # costs nothing that matters.
                    controls.snapshot().strategy.merged(requested).validated()
                changed = controls.apply(requested)
            except ControlError as exc:
                raise HTTPException(status_code=422, detail=f"{exc.field}: {exc}") from exc

            if changed and store is not None:
                # A patch to the running policy is a save: otherwise the file
                # and the loop would disagree until the next explicit save,
                # which is exactly the drift "one source of truth" exists to
                # remove. Inlined rather than hoisted into a helper - the
                # store block below is the only other writer, and it already
                # has store.save(incoming) right there for the same reason.
                store.save(controls.snapshot().strategy)
            if changed:
                # Only when something actually moved. A no-op patch is not a
                # state change and must not fill the log with noise.
                bus.publish(events.ControlChanged(changed=changed, source="web"))

            return _control_payload()

    if runner is not None:

        @app.post("/api/bot/start")
        def start_bot() -> dict:
            try:
                return runner.start()
            except RunnerError as exc:
                # The runner already published a BotError for anything worth
                # seeing in the feed; this is just the caller's answer.
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        @app.post("/api/bot/stop")
        def stop_bot() -> dict:
            return runner.stop()

    if store is not None:

        @app.get("/api/strategies")
        def list_strategies() -> dict:
            return {"active": store.active_name(), "names": store.names()}

        @app.get("/api/strategies/{name}")
        def read_strategy(name: str) -> dict:
            try:
                return store.load(name).to_dict()
            except ControlError as exc:
                # A corrupt profile is not a missing one: load() raises
                # "not_found" for absent and the default "invalid" for
                # unparseable JSON, and the reader deserves to be told which.
                raise HTTPException(
                    status_code=_STATUS_FOR_CODE.get(exc.code, 422), detail=str(exc)
                ) from exc

        @app.put("/api/strategies/{name}")
        def write_strategy(name: str, body: dict[str, Any]) -> dict:
            # The URL's name wins. Otherwise a body naming something else
            # writes a different file and the caller has no way to know
            # where their profile went.
            try:
                incoming = Strategy.from_dict({**body, "name": name})
                store.save(incoming)
            except ControlError as exc:
                raise HTTPException(
                    status_code=422, detail=f"{exc.field}: {exc}"
                ) from exc

            # Saving the profile the bot is currently running IS a live edit.
            if controls is not None and name == store.active_name():
                changed = controls.replace(incoming)
                if changed:
                    bus.publish(events.ControlChanged(changed=changed, source="web"))
            return incoming.to_dict()

        @app.post("/api/strategies/{name}/activate")
        def activate_strategy(name: str) -> dict:
            try:
                loaded = store.load(name)
                store.set_active(name)
            except ControlError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if controls is not None:
                changed = controls.replace(loaded)
                if changed:
                    bus.publish(events.ControlChanged(changed=changed, source="web"))
            return {"active": name, "names": store.names()}

        @app.delete("/api/strategies/{name}")
        def remove_strategy(name: str) -> dict:
            try:
                store.delete(name)
            except ControlError as exc:
                # exc.code, not the message text. Plan 1's final review added
                # a discriminator precisely so this route does not string-match
                # its way to a status: "not_found" when the profile is absent,
                # "conflict" when it exists and the refusal is about what
                # deleting it would leave behind (active, or the last one).
                raise HTTPException(
                    status_code=_STATUS_FOR_CODE.get(exc.code, 422), detail=str(exc)
                ) from exc
            return {"active": store.active_name(), "names": store.names()}

    @app.post("/api/shutdown")
    def shutdown_all() -> dict:
        # This route has no handle on the worker thread or the Server - the
        # shared `shutdown` Event is the only thing it can reach from a
        # request handler. serve_web()'s watcher thread is the one actually
        # waiting on it: it stops the runner and sets server.should_exit,
        # which brings the loop and the server down together.
        #
        # Registered unconditionally, with no `runner is not None` guard: a
        # dashboard that cannot shut itself down is worse than one that can,
        # and ending the process needs no bot to be running at all.
        shutdown.set()
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

    # A write verb against an /api path nothing above registered (e.g.
    # /api/bot/start with no runner wired) must read as "this route does
    # not exist" - 404 - not as the mount's own answer for a method it
    # rejects outright. StaticFiles refuses POST/PUT/PATCH/DELETE before it
    # ever checks whether a matching file exists, which would turn a
    # never-registered route into a misleading 405. GET/HEAD are left to
    # the mount below, which already answers unmatched paths with the SPA's
    # own 404 page.
    #
    # Starlette matches in registration order and this pattern
    # ("/api/{_path:path}") swallows every write-verb request under /api,
    # real or not - so it has to stay the LAST /api route registered. Any
    # new /api route (the strategy CRUD routes are next) MUST be added
    # above this one, not below: a route registered after this catch-all is
    # unreachable and will 404 as if it were never wired at all, which is a
    # much more confusing failure than a normal shadowing bug because it
    # looks identical to "the route was never registered." See
    # test_the_unmatched_api_catch_all_does_not_shadow_real_routes in
    # tests/test_lifecycle_api.py, which pins this ordering.
    @app.api_route("/api/{_path:path}", methods=["POST", "PUT", "PATCH", "DELETE"])
    def unmatched_api_route(_path: str) -> None:
        raise HTTPException(status_code=404, detail="no such route")

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
