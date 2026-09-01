"""Smoke-test the dashboard against a real uvicorn server.

The test suite cannot reach any of this. Starlette's TestClient runs the whole
app coroutine to completion and only then hands back a buffered body, so no
test can see the SSE stream's real response headers, its incremental delivery,
or - the failure that mattered - whether the process can still shut down while
a browser tab is holding a stream open. That one deadlocked the bot silently:
uvicorn waited for the response to finish, the response waited for the client
to disconnect, and the scan loop kept tapping the emulator while the user
believed they had stopped it.

So this runs the real server on a real port and drives it with a real client.
No emulator and no game interaction: the bot itself never runs here.

    PYTHONPATH=. uv run tools/smoke_dashboard.py
"""

from __future__ import annotations

import json
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

import httpx2 as httpx
import uvicorn

import control
import db
import events
from sinks.sse import SseSink
from sinks.state import BotState, StateSink
from web.app import create_app

HOST = "127.0.0.1"
ROUTES_PORT = 8799
SHUTDOWN_PORT = 8801
STOP_PORT = 8802

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))


def seeded_database(directory: Path) -> Path:
    """A finished run with one stored event, as the store sink would leave it."""
    path = directory / "bot.db"
    conn = db.connect(path)
    db.start_run(conn, 1, started_at=1000.0)
    db.insert_event(conn, {
        "seq": 1, "run_id": 1, "ts": 1001.0, "type": "Tapped", "screen": "IN_RUN",
        "action": "Damage", "reason": None, "score": 0.94, "price": 120,
        "wallet": 300, "detail": '{"x": 840, "y": 1520}',
    })
    db.finish_run(conn, 1, started_at=1000.0, ended_at=1060.0, wave=137,
                  coins=4200, tier=3, abandoned=False, scan_count=30, tap_count=1)
    conn.close()
    return path


def serve(app: object, port: int, *, graceful: int = 2) -> uvicorn.Server:
    """Start a real server on its own thread and wait for it to bind."""
    server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=port, log_level="error",
                       timeout_graceful_shutdown=graceful)
    )
    threading.Thread(target=server.run, name=f"uvicorn-{port}", daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError(f"server on port {port} never started")


def check_routes(db_path: Path, unknown: Path) -> None:
    """Every route over real HTTP, including live push down the SSE stream."""
    state, sse, bus = BotState(), SseSink(), events.EventBus(
        start_seq=db.max_seq(db.connect(db_path))
    )
    state_sink = StateSink(state)
    state_sink.start()
    bus.subscribe(state_sink)
    bus.subscribe(sse)

    app = create_app(state=state, sse=sse, bus=bus, db_path=db_path,
                     unknown_dir=unknown)
    server = serve(app, ROUTES_PORT)

    bus.publish(events.ScanCompleted(screen="IN_RUN", duration_ms=91.0, wallet=450))
    bus.publish(events.RunStarted(run_id=2))
    time.sleep(0.3)

    with httpx.Client(base_url=f"http://{HOST}:{ROUTES_PORT}", timeout=10.0) as c:
        r = c.get("/")
        check("GET / serves the page",
              r.status_code == 200
              and r.headers["content-type"].startswith("text/html")
              and "/_next/" in r.text,
              f"{r.status_code} {r.headers.get('content-type')}")

        body = c.get("/api/status").json()
        check("GET /api/status is live state",
              body["screen"] == "IN_RUN" and body["wallet"] == 450
              and body["run"]["id"] == 2 and "dropped" in body, str(body)[:120])

        runs = c.get("/api/runs").json()
        check("GET /api/runs returns the stored run",
              runs and runs[0]["wave"] == 137 and runs[0]["tap_count"] == 1,
              str(runs)[:120])

        stored = c.get("/api/runs/1/events").json()
        check("GET /api/runs/1/events decodes detail",
              stored[0]["detail"] == {"x": 840, "y": 1520}, str(stored[0]))

        shots = c.get("/api/unknown").json()
        check("GET /api/unknown lists snapshots",
              shots and shots[0]["name"] == "1700000000.png", str(shots))
        r = c.get(shots[0]["url"])
        check("a snapshot is served as a png",
              r.status_code == 200 and r.headers["content-type"] == "image/png",
              r.headers.get("content-type", ""))

        seen: list[dict] = []
        ids: list[str] = []
        with c.stream("GET", "/api/events/stream") as r:
            check("the stream sets its own headers",
                  r.headers["content-type"].startswith("text/event-stream")
                  and r.headers["cache-control"] == "no-store", str(dict(r.headers)))
            # Published AFTER the connection is open, so its arrival proves live
            # push rather than replay of the ring.
            threading.Timer(
                0.6, lambda: bus.publish(events.Navigated(target="RETRY"))
            ).start()
            for line in r.iter_lines():
                if line.startswith("id:"):
                    ids.append(line[3:].strip())
                elif line.startswith("data:"):
                    seen.append(json.loads(line[5:]))
                    if len(seen) == 3:
                        break
        check("the ring replays, then pushes live",
              [e["type"] for e in seen] == ["ScanCompleted", "RunStarted", "Navigated"],
              str([e["type"] for e in seen]))
        # The bus was seeded from the stored maximum (1), so it issues 2, 3, 4 -
        # which is the whole point of seeding.
        check("seq continues past the stored maximum", ids == ["2", "3", "4"], str(ids))

        with c.stream("GET", "/api/events/stream",
                      headers={"Last-Event-ID": "2"}) as r:
            resumed = None
            for line in r.iter_lines():
                if line.startswith("data:"):
                    resumed = json.loads(line[5:])
                    break
        check("Last-Event-ID resumes over HTTP",
              resumed is not None and resumed["seq"] == 3
              and resumed["type"] == "RunStarted", str(resumed))

    server.should_exit = True
    state_sink.close()


def shutdown_seconds(db_path: Path, *, hold_stream: bool, set_flag: bool) -> float:
    """Time server.run()'s return after the scan loop ends. -1.0 means it hung."""
    state, sse, bus = BotState(), SseSink(), events.EventBus()
    bus.subscribe(sse)
    stop = threading.Event()
    app = create_app(state=state, sse=sse, bus=bus, db_path=db_path,
                     unknown_dir=db_path.parent / "unknown", stop=stop)
    server = serve(app, SHUTDOWN_PORT)

    # Watch the serving thread itself: it ends exactly when run() returns.
    serving = [t for t in threading.enumerate() if t.name == f"uvicorn-{SHUTDOWN_PORT}"][0]

    sock = socket.create_connection((HOST, SHUTDOWN_PORT), timeout=5)
    path = "/api/events/stream" if hold_stream else "/api/status"
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    sock.recv(200)  # headers are out; a stream's response stays open
    bus.publish(events.Navigated(target="RETRY"))
    time.sleep(0.5)

    # Exactly what serve_web's worker does when run_forever returns.
    started = time.monotonic()
    server.should_exit = True
    if set_flag:
        stop.set()
    serving.join(timeout=20)
    elapsed = time.monotonic() - started
    sock.close()
    return -1.0 if serving.is_alive() else elapsed


def check_stop_route_stops_serve_web(tmp: Path) -> None:
    """The Critical the whole-branch review reproduced: POST
    /api/control/stop set the shared `stop` Event, but nothing in serve_web()
    ever waited on it - only event_stream() did. The route killed every SSE
    connection (so the dashboard's feed went dead and looked stopped) while
    the scan loop and the server both kept right on running underneath.

    Drives the real serve_web() - a real uvicorn Server, no monkeypatching -
    with a fake bot (no emulator needed) and proves it actually returns after
    a browser's stop request, and that the loop stops scanning. This is
    exactly the kind of process-lifecycle behaviour this file's own docstring
    says pytest cannot observe.
    """
    import tower_bot

    class FakeBot:
        """Enough of TowerBot's shape for serve_web(): a controls.interval to
        size the shutdown join, a run_forever() that loops harmlessly until
        told to stop, and a stop() that flips both."""

        def __init__(self) -> None:
            self.controls = control.Controls(interval=0.05)
            self._running = True
            self._stopping = threading.Event()
            self.scans = 0

        def run_forever(self, max_runs: int | None = None) -> None:
            while self._running:
                self.scans += 1
                self._stopping.wait(self.controls.interval)

        def stop(self, *_: object) -> None:
            self._running = False
            self._stopping.set()

    bot = FakeBot()
    state, sse, bus = BotState(), SseSink(), events.EventBus()
    stop = threading.Event()
    unknown = tmp / "stop-route-unknown"
    unknown.mkdir(exist_ok=True)
    app = create_app(
        state=state, sse=sse, bus=bus, db_path=None, unknown_dir=unknown,
        stop=stop, controls=bot.controls, checks={"brightness": object()},
    )

    result: dict[str, Any] = {}

    def _run() -> None:
        started = time.monotonic()
        tower_bot.serve_web(bot, app, host=HOST, port=STOP_PORT, max_runs=None, stop=stop)
        result["elapsed"] = time.monotonic() - started
        result["returned"] = True

    runner = threading.Thread(target=_run, name="serve-web-under-test", daemon=True)
    runner.start()

    for _ in range(100):
        try:
            with socket.create_connection((HOST, STOP_PORT), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError(f"serve_web never started on port {STOP_PORT}")

    time.sleep(0.3)  # let a few scans happen, matching the reproduction

    with httpx.Client(base_url=f"http://{HOST}:{STOP_PORT}", timeout=10.0) as c:
        r = c.post("/api/control/stop")
        check("POST /api/control/stop returns 200",
              r.status_code == 200 and r.json() == {"stopping": True}, str(r.text))

    runner.join(timeout=8.0)
    check("serve_web returns after a browser stop",
          not runner.is_alive() and result.get("returned") is True,
          f"still running after 8s (elapsed={result.get('elapsed')})")
    check("bot._running is False after a browser stop",
          bot._running is False, f"_running={bot._running}")

    scans_at_stop = bot.scans
    time.sleep(0.3)
    check("scanning stopped after the browser's stop request",
          bot.scans == scans_at_stop,
          f"kept scanning after stop: {scans_at_stop} -> {bot.scans}")


def main() -> int:
    signal.alarm(180)  # this script must never hang a session
    tmp = Path(mkdtemp())
    unknown = tmp / "unknown"
    unknown.mkdir()
    (unknown / "1700000000.png").write_bytes(b"fake png")
    db_path = seeded_database(tmp)

    check_routes(db_path, unknown)

    # The regression that motivated this file. A stream held open must not stop
    # the process from exiting once the scan loop is done.
    for label, hold, flag in (
        ("a plain request (control)", False, True),
        ("an SSE stream held open", True, True),
        ("a stream open, stop flag suppressed (backstop only)", True, False),
    ):
        elapsed = shutdown_seconds(db_path, hold_stream=hold, set_flag=flag)
        check(f"shutdown with {label}", elapsed >= 0.0,
              "did not return within 20s")
        if elapsed >= 0.0:
            results[-1] = (f"{results[-1][0]} -> {elapsed:.1f}s", True, "")
        time.sleep(0.5)

    # The Critical this file's docstring exists for: the Stop button itself,
    # driven end to end against the real serve_web().
    check_stop_route_stops_serve_web(tmp)

    width = max(len(label) for label, _, _ in results)
    for label, ok, detail in results:
        # Details are diagnostics: they belong on a failure, not on every line.
        print(f"{'PASS' if ok else 'FAIL'}  {label:<{width}}  {'' if ok else detail}")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
