"""The dashboard's HTTP layer: JSON for the machine, one HTML file for the eye.

Two sources, deliberately separate. "What is the bot doing right now" is a
memory question, answered from the shared BotState. "What did it do" is a
history question, answered by read-only SQLite connections opened per request
- the web layer never writes, and cannot: db.reader() opens mode=ro.

Binds 127.0.0.1. No auth, and /api/unknown serves screenshots of a live
session - see the warning beside config.WEB_HOST before changing that.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

import config
import db
from events import EventBus
from sinks.sse import SseSink
from sinks.state import BotState

STATIC_DIR = Path(__file__).parent / "static"

# One page of history is plenty for a dashboard, and it bounds the response
# whatever the query string asks for.
MAX_RUNS_PER_PAGE = 500


def create_app(
    *,
    state: BotState,
    sse: SseSink,
    bus: EventBus,
    db_path: Path = config.DB_PATH,
    unknown_dir: Path = config.UNKNOWN_DIR,
) -> FastAPI:
    app = FastAPI(title="The Tower bot")

    @app.get("/api/status")
    def status() -> dict:
        payload = state.snapshot()
        # Dropped events are the bus's business, not the state's: they were
        # never delivered to a sink, so no accumulator ever saw them.
        payload["dropped"] = bus.dropped
        return payload

    @app.get("/api/runs")
    def runs(limit: int = 50) -> list[dict]:
        with db.reader(db_path) as conn:
            return db.list_runs(conn, limit=max(1, min(limit, MAX_RUNS_PER_PAGE)))

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: int) -> list[dict]:
        with db.reader(db_path) as conn:
            return db.run_events(conn, run_id)

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
