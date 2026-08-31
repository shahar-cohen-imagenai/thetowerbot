# Tower Bot Persistence and Dashboard (Phase 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every state-changing event and every run to SQLite, and serve a localhost dashboard that shows what the bot is doing right now and what it did all week.

**Architecture:** Two new sinks on the existing bus. `StoreSink` owns a SQLite writer on its consumer thread; `SseSink` owns a lock-protected ring of the last 500 events that the web layer reads from. `db.py` holds the SQL, so the web layer reads the database without importing a sink and the sink is testable without a web server. Under `--web`, uvicorn runs on the main thread and the scan loop moves to a worker thread — that way round because uvicorn's signal handling only works on the main thread, and because `cv2.matchTemplate` releases the GIL, so the two genuinely run in parallel.

**Tech Stack:** Python 3.12, uv, adbutils, opencv-python, numpy, rich, pytest. **New deps:** `fastapi`, `uvicorn` (runtime), `httpx` (dev, for `TestClient`). SQLite is stdlib. The dashboard is one static HTML file — no framework, no build step.

**Spec:** `docs/superpowers/specs/2026-08-30-tower-bot-observability-design.md` (sections 8, 9, 3, 4, 13)

## Global Constraints

- Python >= 3.12. Every function gets type hints. `from __future__ import annotations` at the top of every module.
- Dependencies are managed with `uv add` / `uv add --dev` — **never hand-edit `pyproject.toml`**.
- **The bus invariant is absolute:** `offer()` must never block. Both new sinks obey it — `StoreSink` through the bounded queue it inherits, `SseSink` through a `deque.append` under a short lock.
- **The web layer never writes to the database.** Reads open their own short-lived read-only connection per request; the only writer is the store sink's consumer thread.
- **Bind `127.0.0.1`.** The dashboard serves screenshots of a live session and has no auth. Localhost is the security boundary; the warning lives in `config.py` beside the host setting.
- The full test suite must pass with **no emulator attached** and **no browser**. Device access is mocked; the web layer is exercised through `fastapi.testclient.TestClient`.
- **Degrade, never crash.** A locked database, a full queue, or a disconnected browser must cost events, never the scan loop.
- Run `uv run pytest -q` before every commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `db.py` (new) | Schema, connections, inserts, queries, pruning. Pure SQL — knows rows, not events |
| `sinks/store.py` (new) | `StoreSink`: event → row translation, run aggregates, the writer thread |
| `sinks/state.py` (new) | `BotState`: live counters accumulated from the stream, shared by the TUI and `/api/status`; `StateSink` |
| `sinks/sse.py` (new) | `SseSink`: the ring buffer the browser's live feed reads from |
| `web/app.py` (new) | FastAPI app factory: JSON routes, SSE endpoint, dashboard route |
| `web/static/index.html` (new) | The dashboard: vanilla JS, `EventSource`, a canvas sparkline |
| `config.py` (modify) | DB path, retention, web host/port, SSE ring and poll settings |
| `sinks/tui.py` (modify) | Drop `TuiState`; render from the shared `BotState` |
| `runs.py` (modify) | `RunTracker(start_id=…)` so run ids survive a restart |
| `tower_bot.py` (modify) | `--web` / `--db` / `--no-store`, seeding, the threading arrangement |
| `tests/test_db.py` (new) | Schema round-trip, seeds, upserts, pruning, queries |
| `tests/test_store_sink.py` (new) | Row mapping, the two events that are never stored, run aggregates |
| `tests/test_state.py` (new) | The shared accumulator |
| `tests/test_sse_sink.py` (new) | Ring ordering, replay, eviction |
| `tests/test_web_api.py` (new) | Every route, through `TestClient` |

**Why `db.py` and not `store.py`:** the spec's module layout names only `sinks/store.py`. Splitting the SQL out of the sink is an addition to that layout, not a contradiction — but two files both called `store.py` would be a permanent source of import confusion, so the SQL layer takes a distinct name.

---

### Task 1: The database layer

**Files:**
- Create: `db.py`
- Modify: `config.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing — stdlib `sqlite3` only.
- Produces: `db.SCHEMA`, `db.connect(path) -> sqlite3.Connection`, `db.reader(path)` (context manager), `db.max_seq(conn) -> int`, `db.max_run_id(conn) -> int`, `db.insert_event(conn, row: dict) -> None`, `db.start_run(conn, run_id: int, started_at: float) -> None`, `db.finish_run(conn, run_id: int, *, started_at: float, ended_at: float, wave: int | None, coins: int | None, tier: int | None, abandoned: bool, scan_count: int, tap_count: int) -> None`, `db.list_runs(conn, limit: int = 50) -> list[dict]`, `db.run_events(conn, run_id: int, limit: int = 2000) -> list[dict]`, `db.prune_events(conn, retention_days: int, now: float | None = None) -> int`; `config.DB_PATH`, `config.EVENT_RETENTION_DAYS`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_db.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Add the config constants**

Append to `config.py`:

```python
# --- Persistence ----------------------------------------------------------
DB_PATH: Path = Path(__file__).parent / "tower_bot.db"

# Events older than this are deleted at startup. ScanCompleted is never
# stored - it fires every 2s, roughly 43,000 near-identical rows a day - so
# what remains is state changes only, and 30 days of those stays small.
EVENT_RETENTION_DAYS: int = 30
```

- [ ] **Step 4: Write `db.py`**

```python
"""SQLite persistence for the event stream.

Pure database mechanics: schema, writes, queries. It knows rows, not events -
turning an event into a row is `sinks/store.py`'s job. Keeping the two apart
lets the web layer read the database without importing a sink, and lets the
sink be tested without a web server.

Concurrency: WAL, one writer (the store sink's consumer thread), many readers
(the web layer). Readers open their own short-lived connection, because a
sqlite3 connection belongs to the thread that created it and FastAPI runs sync
routes on a threadpool.

Typed columns for what is filtered and aggregated, a JSON `detail` blob for
the long tail - so adding a field to an event needs no migration.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY,
    started_at REAL    NOT NULL,
    ended_at   REAL,
    wave       INTEGER,
    coins      INTEGER,
    tier       INTEGER,
    abandoned  INTEGER NOT NULL DEFAULT 0,
    scan_count INTEGER NOT NULL DEFAULT 0,
    tap_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    seq    INTEGER PRIMARY KEY,
    run_id INTEGER,
    ts     REAL NOT NULL,
    type   TEXT NOT NULL,
    screen TEXT,
    action TEXT,
    reason TEXT,
    score  REAL,
    price  INTEGER,
    wallet INTEGER,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS events_run_idx ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS events_ts_idx  ON events(ts);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the writable connection, creating the file and schema if needed."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # WAL is what lets the web layer read while the sink writes.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


@contextmanager
def reader(path: Path | str) -> Iterator[sqlite3.Connection]:
    """A short-lived read-only connection, closed on exit.

    Read-only at the driver level rather than by convention: the web layer is
    the one component that should never be able to write, and a URI opened
    with mode=ro cannot, whatever a route does with it.
    """
    uri = f"{Path(path).absolute().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def max_seq(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0])


def max_run_id(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM runs").fetchone()[0])


def insert_event(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO events
               (seq, run_id, ts, type, screen, action, reason, score, price,
                wallet, detail)
           VALUES (:seq, :run_id, :ts, :type, :screen, :action, :reason,
                   :score, :price, :wallet, :detail)""",
        row,
    )
    conn.commit()


def start_run(conn: sqlite3.Connection, run_id: int, started_at: float) -> None:
    conn.execute(
        """INSERT INTO runs (id, started_at) VALUES (?, ?)
           ON CONFLICT(id) DO UPDATE SET started_at = excluded.started_at""",
        (run_id, started_at),
    )
    conn.commit()


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    started_at: float,
    ended_at: float,
    wave: int | None,
    coins: int | None,
    tier: int | None,
    abandoned: bool,
    scan_count: int,
    tap_count: int,
) -> None:
    """Close a run out.

    An upsert rather than an update: if the process died mid-run and the
    opening row never landed, the run is still worth recording. `started_at`
    is only used in that case - an existing row keeps the start it was opened
    with, which is the real one.
    """
    conn.execute(
        """INSERT INTO runs (id, started_at, ended_at, wave, coins, tier,
                             abandoned, scan_count, tap_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               ended_at   = excluded.ended_at,
               wave       = excluded.wave,
               coins      = excluded.coins,
               tier       = excluded.tier,
               abandoned  = excluded.abandoned,
               scan_count = excluded.scan_count,
               tap_count  = excluded.tap_count""",
        (
            run_id, started_at, ended_at, wave, coins, tier,
            int(abandoned), scan_count, tap_count,
        ),
    )
    conn.commit()


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def run_events(
    conn: sqlite3.Connection, run_id: int, limit: int = 2000
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM events WHERE run_id = ? ORDER BY seq LIMIT ?",
        (run_id, limit),
    ).fetchall()
    return [_decode(row) for row in rows]


def prune_events(
    conn: sqlite3.Connection, retention_days: int, now: float | None = None
) -> int:
    """Delete events older than the retention window. Returns how many went."""
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    cursor = conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    raw = data.get("detail")
    data["detail"] = json.loads(raw) if raw else {}
    return data
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db.py -q`
Expected: PASS (8 tests)

- [ ] **Step 6: Make sure the database file never gets committed**

Run:

```bash
grep -q '^tower_bot.db' .gitignore || printf '\n# The event log is machine-local state, not source.\ntower_bot.db\ntower_bot.db-wal\ntower_bot.db-shm\n' >> .gitignore
```

- [ ] **Step 7: Run the whole suite and commit**

```bash
uv run pytest -q
git add db.py tests/test_db.py config.py .gitignore
git commit -m "feat: add the SQLite schema and query layer"
```

---

### Task 2: The store sink

**Files:**
- Create: `sinks/store.py`
- Modify: `docs/superpowers/specs/2026-08-30-tower-bot-observability-design.md` (section 13, third open item)
- Test: `tests/test_store_sink.py`

**Interfaces:**
- Consumes: `db.connect`, `db.insert_event`, `db.start_run`, `db.finish_run`, `sinks.base.QueueSink`, `events.*`
- Produces: `sinks.store.to_row(event: events.Event, run_id: int | None) -> dict[str, Any]`, `sinks.store.StoreSink(path: Path | str = config.DB_PATH, maxsize: int = 1000)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store_sink.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_sink.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sinks.store'`

- [ ] **Step 3: Write `sinks/store.py`**

```python
"""Persist state-changing events to SQLite.

The consumer thread this sink inherits from QueueSink is the database's only
writer, which is what lets the web layer read the same file concurrently. The
connection is opened on that thread rather than in start(), because a sqlite3
connection belongs to the thread that created it.

Two events are deliberately NOT stored:

* `ScanCompleted`. It fires every 2s - roughly 43,000 near-identical rows a
  day. It answers "is it alive right now", which is a question for the TUI and
  the live feed, not for history. The run row carries `scan_count` instead.
* `ScreenChanged` where prev == curr. Booting onto an unmodelled screen emits
  exactly one of these (the tracker's UNKNOWN placeholder confirming itself).
  It is a state change that changed no state, and the events table is meant to
  hold only rows that mean something.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path
from typing import Any

import config
import db
import events
from sinks.base import QueueSink

# The columns the schema keeps typed. Everything else on an event goes to the
# JSON `detail` blob, so adding a field to an event needs no migration.
_TYPED = ("screen", "action", "reason", "score", "price", "wallet")


def to_row(event: events.Event, run_id: int | None) -> dict[str, Any]:
    """Flatten an event into a row for the events table."""
    data = dataclasses.asdict(event)
    seq = data.pop("seq")
    ts = data.pop("ts")
    # RunStarted / RunEnded name their own run; that belongs in the column,
    # not repeated inside the blob.
    own_run = data.pop("run_id", None)
    if "curr" in data:  # ScreenChanged: `curr` is the screen
        data["screen"] = data.pop("curr")

    row: dict[str, Any] = {
        "seq": seq,
        "ts": ts,
        "type": event.type,
        "run_id": own_run if own_run is not None else run_id,
    }
    for column in _TYPED:
        row[column] = data.pop(column, None)
    # default=str is a guard, not a plan: everything published today is
    # JSON-native, and a future field that is not degrades to a string here
    # rather than killing the writer thread.
    row["detail"] = json.dumps(data, default=str) if data else None
    return row


class StoreSink(QueueSink):
    def __init__(
        self, path: Path | str = config.DB_PATH, maxsize: int = 1000
    ) -> None:
        super().__init__(maxsize=maxsize)
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._run_id: int | None = None
        self._scans = 0
        self._taps = 0

    def _consume(self) -> None:
        self._conn = db.connect(self.path)
        try:
            super()._consume()
        finally:
            self._conn.close()
            self._conn = None

    def handle(self, event: events.Event) -> None:
        conn = self._conn
        if conn is None:  # handle() called outside the consumer thread
            return

        match event:
            case events.ScanCompleted():
                self._scans += 1
                return
            case events.ScreenChanged() if event.prev == event.curr:
                return
            case events.RunStarted():
                self._run_id = event.run_id
                self._scans = 0
                self._taps = 0
                db.start_run(conn, event.run_id, event.ts)
            case events.Tapped():
                self._taps += 1
            case events.RunEnded():
                db.finish_run(
                    conn,
                    event.run_id,
                    started_at=event.ts - event.duration,
                    ended_at=event.ts,
                    wave=event.wave,
                    coins=event.coins,
                    tier=event.tier,
                    abandoned=event.abandoned,
                    scan_count=self._scans,
                    tap_count=self._taps,
                )

        db.insert_event(conn, to_row(event, self._run_id))

        if isinstance(event, events.RunEnded):
            self._run_id = None
            self._scans = 0
            self._taps = 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store_sink.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Close the spec's open item**

In `docs/superpowers/specs/2026-08-30-tower-bot-observability-design.md`, section 13, replace the third bullet ("Booting onto an unmodelled screen emits one `ScreenChanged`…") with:

```markdown
- **Resolved in phase 4.** Booting onto an unmodelled screen emits one
  `ScreenChanged` with `prev == curr == "UNKNOWN"` (at most once per launch).
  The store sink drops it: a state change that changed no state is not
  history. It still reaches the TUI and the live feed, where it is a useful
  "I looked and did not recognise this" signal.
```

- [ ] **Step 6: Run the whole suite and commit**

```bash
uv run pytest -q
git add sinks/store.py tests/test_store_sink.py docs/superpowers/specs/2026-08-30-tower-bot-observability-design.md
git commit -m "feat: persist state-changing events and run stats to SQLite"
```

---

### Task 3: One live-state accumulator, shared by the TUI and the API

The TUI already derives "what is the bot doing right now" from the stream, and `/api/status` needs the same answer. Two accumulators would drift; this task extracts the one that exists and grows it the fields the dashboard needs.

**Files:**
- Create: `sinks/state.py`
- Modify: `sinks/tui.py`
- Modify: `tests/test_tui_sink.py` (imports only)
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `sinks.log.render`, `sinks.base.QueueSink`, `events.*`
- Produces: `sinks.state.BotState(tail: int = 12)` with `.apply(event)`, `.uptime`, `.snapshot() -> dict[str, Any]`, and the attributes `screen`, `scans`, `taps`, `skips`, `last_error`, `tail`, `run_id`, `run_started`, `runs_completed`, `wallet`, `run_taps`; `sinks.state.StateSink(state: BotState, maxsize: int = 1000)`
- Note for later tasks: `sinks.tui.TuiState` is **gone**. `TuiSink(state: BotState | None = None)` takes the shared state.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_state.py`:

```python
from __future__ import annotations

import events
from sinks.state import BotState


def stamped(event: events.Event) -> events.Event:
    return events.EventBus().publish(event)


def test_a_run_opens_and_closes_the_current_run_panel() -> None:
    state = BotState()

    state.apply(stamped(events.RunStarted(run_id=4)))
    assert state.snapshot()["run"]["id"] == 4

    state.apply(stamped(events.RunEnded(run_id=4, duration=60.0)))
    assert state.snapshot()["run"] is None
    assert state.snapshot()["runs_completed"] == 1


def test_taps_are_counted_both_overall_and_for_the_current_run() -> None:
    state = BotState()
    state.apply(stamped(events.Tapped(action="Damage", x=1, y=2, score=0.9)))
    state.apply(stamped(events.RunStarted(run_id=1)))
    state.apply(stamped(events.Tapped(action="Damage", x=1, y=2, score=0.9)))

    snapshot = state.snapshot()

    assert snapshot["taps"]["Damage"] == 2
    assert snapshot["run"]["taps"]["Damage"] == 1


def test_the_wallet_tracks_the_latest_scan() -> None:
    state = BotState()
    state.apply(stamped(events.ScanCompleted(screen="IN_RUN", duration_ms=9.0, wallet=450)))

    assert state.snapshot()["wallet"] == 450


def test_skips_are_counted_by_reason() -> None:
    state = BotState()
    state.apply(stamped(events.Skipped(action="Damage", reason="unaffordable")))
    state.apply(stamped(events.Skipped(action="Damage", reason="unaffordable")))
    state.apply(stamped(events.Skipped(action="*", reason="screen_gated")))

    assert state.snapshot()["skips"] == {"unaffordable": 2, "screen_gated": 1}


def test_the_snapshot_is_json_serialisable() -> None:
    """It is handed straight to FastAPI; a Counter in it would 500 the route."""
    import json

    state = BotState()
    state.apply(stamped(events.RunStarted(run_id=1)))
    state.apply(stamped(events.Tapped(action="Damage", x=1, y=2, score=0.9)))
    state.apply(stamped(events.BotError(message="device gone")))

    json.dumps(state.snapshot())  # must not raise


def test_the_last_error_is_kept() -> None:
    state = BotState()
    state.apply(stamped(events.BotError(message="device gone")))

    assert state.snapshot()["last_error"] == "device gone"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sinks.state'`

- [ ] **Step 3: Write `sinks/state.py`**

```python
"""What the bot is doing right now, accumulated from the event stream.

The TUI panel and the dashboard's /api/status answer the same question, so
they share one accumulator instead of each deriving it and drifting apart.
No terminal, no web server, no database - just state, so it tests directly.

Everything is guarded by a lock. The events arrive on a sink's consumer
thread while the web layer reads snapshots from request threads, and copying
a Counter that is being mutated raises "dictionary changed size during
iteration". The lock is held for microseconds and never while doing I/O.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Any

import events
from sinks.base import QueueSink
from sinks.log import render


class BotState:
    def __init__(self, tail: int = 12) -> None:
        self._lock = threading.Lock()
        self.screen = "UNKNOWN"
        self.scans = 0
        self.taps: Counter[str] = Counter()
        self.skips: Counter[str] = Counter()
        self.last_error: str | None = None
        self.started = time.monotonic()
        self.tail: deque[str] = deque(maxlen=tail)
        self.run_id: int | None = None
        self.run_started: float | None = None
        self.runs_completed = 0
        self.wallet: int | None = None
        self.run_taps: Counter[str] = Counter()

    def apply(self, event: events.Event) -> None:
        with self._lock:
            match event:
                case events.ScreenChanged():
                    self.screen = event.curr
                    self.tail.append(render(event))
                case events.ScanCompleted():
                    self.scans += 1
                    self.screen = event.screen
                    self.wallet = event.wallet
                case events.Tapped():
                    self.taps[event.action] += 1
                    self.run_taps[event.action] += 1
                    self.tail.append(render(event))
                case events.Skipped():
                    self.skips[event.reason] += 1
                case events.RunStarted():
                    self.run_id = event.run_id
                    self.run_started = event.ts
                    self.run_taps = Counter()
                    self.tail.append(render(event))
                case events.RunEnded():
                    self.run_id = None
                    self.run_started = None
                    self.runs_completed += 1
                    self.tail.append(render(event))
                case events.BotError():
                    self.last_error = event.message
                    self.tail.append(render(event))
                case _:
                    self.tail.append(render(event))

    @property
    def uptime(self) -> float:
        return time.monotonic() - self.started

    def snapshot(self) -> dict[str, Any]:
        """A JSON-safe copy for /api/status.

        `run_started` is wall clock (the event's ts), so elapsed is measured
        against time.time(); uptime is monotonic and measured against its own
        clock. Mixing the two would be a bug the moment the host's clock moves.
        """
        with self._lock:
            run: dict[str, Any] | None = None
            if self.run_id is not None and self.run_started is not None:
                run = {
                    "id": self.run_id,
                    "started_at": self.run_started,
                    "elapsed": round(time.time() - self.run_started, 1),
                    "taps": dict(self.run_taps),
                }
            return {
                "screen": self.screen,
                "uptime": round(self.uptime, 1),
                "scans": self.scans,
                "taps": dict(self.taps),
                "skips": dict(self.skips),
                "runs_completed": self.runs_completed,
                "run": run,
                "wallet": self.wallet,
                "last_error": self.last_error,
                "tail": list(self.tail),
            }


class StateSink(QueueSink):
    """Feeds a BotState off the bus when no TUI is doing it already."""

    def __init__(self, state: BotState, maxsize: int = 1000) -> None:
        super().__init__(maxsize=maxsize)
        self.state = state

    def handle(self, event: events.Event) -> None:
        self.state.apply(event)
```

- [ ] **Step 4: Point the TUI at the shared state**

In `sinks/tui.py`: delete the whole `TuiState` class, and replace the module docstring and the `TuiSink.__init__` with:

```python
"""Live terminal panel.

The state it draws lives in sinks/state.py, shared with the dashboard's
/api/status: the panel and the web header answer the same question, and one
accumulator is the only way they cannot disagree. TuiSink only draws.
"""

from __future__ import annotations

import events
from sinks.base import QueueSink
from sinks.state import BotState


class TuiSink(QueueSink):
    def __init__(self, state: BotState | None = None, maxsize: int = 1000) -> None:
        super().__init__(maxsize=maxsize)
        self.state = state if state is not None else BotState()
        self._live = None
```

The rest of the file (`start`, `close`, `handle`, `_render`) is unchanged — it already only touches `self.state`. Remove the now-unused `time`, `Counter`, `deque` and `render` imports.

- [ ] **Step 5: Update the TUI tests' imports**

In `tests/test_tui_sink.py`, replace `from sinks.tui import TuiState` (or `TuiSink, TuiState`) with `from sinks.state import BotState` plus `from sinks.tui import TuiSink` as needed, and rename every `TuiState(` construction to `BotState(`. Change nothing else — the assertions are still the contract.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_state.py tests/test_tui_sink.py -q`
Expected: PASS

- [ ] **Step 7: Run the whole suite and commit**

```bash
uv run pytest -q
git add sinks/state.py sinks/tui.py tests/test_state.py tests/test_tui_sink.py
git commit -m "refactor: share one live-state accumulator between the TUI and the API"
```

---

### Task 4: The SSE ring buffer

**Files:**
- Create: `sinks/sse.py`
- Modify: `config.py`
- Test: `tests/test_sse_sink.py`

**Interfaces:**
- Consumes: `events.*`
- Produces: `sinks.sse.SseSink(capacity: int = config.SSE_RING_SIZE)` with `.offer(event) -> bool`, `.since(seq: int) -> list[events.Event]`; `sinks.sse.to_payload(event) -> dict[str, Any]`; `config.SSE_RING_SIZE`, `config.SSE_POLL_SECONDS`, `config.SSE_HEARTBEAT_SECONDS`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sse_sink.py`:

```python
from __future__ import annotations

import json
import threading
import time

import events
from sinks.sse import SseSink, to_payload


def bus_with(sink: SseSink) -> events.EventBus:
    bus = events.EventBus()
    bus.subscribe(sink)
    return bus


def test_events_come_back_in_order_after_a_given_seq() -> None:
    sink = SseSink(capacity=10)
    bus = bus_with(sink)
    for _ in range(4):
        bus.publish(events.Navigated(target="RETRY"))

    assert [e.seq for e in sink.since(2)] == [3, 4]


def test_a_fresh_client_asking_from_zero_gets_everything_buffered() -> None:
    sink = SseSink(capacity=10)
    bus = bus_with(sink)
    bus.publish(events.Navigated(target="RETRY"))

    assert len(sink.since(0)) == 1


def test_the_ring_evicts_the_oldest_beyond_its_capacity() -> None:
    """500 events of history, not unbounded memory in a process that runs for days."""
    sink = SseSink(capacity=3)
    bus = bus_with(sink)
    for _ in range(5):
        bus.publish(events.Navigated(target="RETRY"))

    assert [e.seq for e in sink.since(0)] == [3, 4, 5]


def test_offer_never_blocks_and_never_refuses() -> None:
    """The bus invariant: a subscribed browser must not be able to stall a scan."""
    sink = SseSink(capacity=4)
    started = time.monotonic()
    results = [sink.offer(events.Navigated(target="RETRY")) for _ in range(5000)]

    assert time.monotonic() - started < 0.5
    assert all(results)


def test_concurrent_writers_and_readers_do_not_corrupt_the_ring() -> None:
    """The scan loop publishes while request threads read; both take the lock."""
    sink = SseSink(capacity=64)
    stop = threading.Event()

    def writer() -> None:
        while not stop.is_set():
            sink.offer(events.Navigated(target="RETRY"))

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        for _ in range(500):
            assert isinstance(sink.since(0), list)
    finally:
        stop.set()
        thread.join(timeout=2)


def test_the_payload_names_its_type_and_is_json_serialisable() -> None:
    event = events.EventBus().publish(
        events.Tapped(action="Damage", x=840, y=1520, score=0.94, price=120)
    )

    body = json.loads(json.dumps(to_payload(event)))

    assert body["type"] == "Tapped"
    assert body["action"] == "Damage"
    assert body["seq"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sse_sink.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sinks.sse'`

- [ ] **Step 3: Add the config constants**

Append to `config.py`:

```python
# --- Live feed ------------------------------------------------------------
# How much history the SSE ring holds. A reconnecting browser replays from
# here using Last-Event-ID, so this is also how long a laptop can sleep
# before the feed has a hole in it.
SSE_RING_SIZE: int = 500
# How often the stream endpoint looks for new events. The scan interval is 2s,
# so a quarter of a second is already imperceptible.
SSE_POLL_SECONDS: float = 0.25
# An idle stream sends a comment this often so proxies and browsers do not
# decide the connection died.
SSE_HEARTBEAT_SECONDS: float = 15.0
```

- [ ] **Step 4: Write `sinks/sse.py`**

```python
"""The live event feed the browser subscribes to.

A ring of the last N events plus a `since(seq)` read. That is the whole
mechanism: the SSE endpoint polls the ring from the asyncio side, and a
reconnecting browser replays from its Last-Event-ID against the same buffer.

Why a ring rather than per-client asyncio queues fed by call_soon_threadsafe:
events are published from the scan loop's thread and consumed by the event
loop, and a shared lock-protected deque is the smallest correct thing that
crosses that boundary. It also makes reconnect-replay fall out for free
instead of needing a second buffer beside the queues.

This is the one sink with no consumer thread, and it does not need one:
offer() appends under a lock held for a few instructions, so it cannot block
and the bus invariant holds.
"""

from __future__ import annotations

import dataclasses
import threading
from collections import deque
from typing import Any

import config
import events


def to_payload(event: events.Event) -> dict[str, Any]:
    """The JSON body of one SSE message."""
    data = dataclasses.asdict(event)
    # `type` is a property, so asdict() does not include it - and it is the
    # first thing the browser switches on.
    data["type"] = event.type
    return data


class SseSink:
    def __init__(self, capacity: int = config.SSE_RING_SIZE) -> None:
        self._lock = threading.Lock()
        self._ring: deque[events.Event] = deque(maxlen=capacity)

    def offer(self, event: events.Event) -> bool:
        with self._lock:
            self._ring.append(event)
        return True

    def since(self, seq: int) -> list[events.Event]:
        """Every buffered event after `seq`, oldest first."""
        with self._lock:
            return [event for event in self._ring if event.seq > seq]

```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sse_sink.py -q`
Expected: PASS (6 tests)

- [ ] **Step 6: Run the whole suite and commit**

```bash
uv run pytest -q
git add sinks/sse.py tests/test_sse_sink.py config.py
git commit -m "feat: add the SSE ring buffer sink"
```

---

### Task 5: The JSON API

**Files:**
- Create: `web/__init__.py`, `web/app.py`
- Modify: `pyproject.toml` (via `uv add` — never by hand)
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `db.reader`, `db.list_runs`, `db.run_events`, `sinks.state.BotState.snapshot`, `sinks.sse.SseSink`, `events.EventBus.dropped`, `config.UNKNOWN_DIR`, `config.DB_PATH`
- Produces: `web.app.create_app(*, state: BotState, sse: SseSink, bus: EventBus, db_path: Path = config.DB_PATH, unknown_dir: Path = config.UNKNOWN_DIR) -> FastAPI`, serving `GET /api/status`, `GET /api/runs`, `GET /api/runs/{run_id}/events`, `GET /api/unknown`, `GET /api/unknown/{name}`

- [ ] **Step 1: Add the dependencies**

```bash
uv add fastapi
uv add --dev httpx
```

`httpx` is a dev dependency because `fastapi.testclient.TestClient` is built on it; nothing at runtime imports it.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_web_api.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_web_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'web'`

- [ ] **Step 4: Create the package and the app factory**

Create the package marker and the module:

```bash
mkdir -p web && touch web/__init__.py
```

Create `web/app.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web_api.py -q`
Expected: PASS (8 tests)

- [ ] **Step 6: Run the whole suite and commit**

```bash
uv run pytest -q
git add web/ tests/test_web_api.py pyproject.toml uv.lock
git commit -m "feat: serve status, run history and unknown snapshots over HTTP"
```

---

### Task 6: The live stream

**Files:**
- Modify: `web/app.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `SseSink.since`, `sinks.sse.to_payload`, `config.SSE_POLL_SECONDS`, `config.SSE_HEARTBEAT_SECONDS`
- Produces: `GET /api/events/stream` — `text/event-stream`, each message `id: <seq>` + `data: <json>`, honouring the `Last-Event-ID` header and a `?since=<seq>` query fallback

**Note on the wire format:** messages carry **no `event:` field**. A named SSE event is only delivered to `addEventListener(name, …)`, so naming them would mean the dashboard had to enumerate every event class and silently miss any added later. The type is in the JSON body instead, where the feed's filter can reach it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_api.py`:

```python
def read_events(response, count: int) -> list[dict]:
    """Pull `count` data frames off an open SSE response, then let it close."""
    import json

    seen: list[dict] = []
    for line in response.iter_lines():
        if line.startswith("data:"):
            seen.append(json.loads(line[len("data:"):]))
            if len(seen) == count:
                break
    return seen


def test_the_stream_replays_what_is_buffered_to_a_fresh_client(harness) -> None:
    client, _, _, bus, _, _ = harness
    bus.publish(events.RunStarted(run_id=1))
    bus.publish(events.Tapped(action="Damage", x=1, y=2, score=0.9))

    with client.stream("GET", "/api/events/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        seen = read_events(response, 2)

    assert [event["type"] for event in seen] == ["RunStarted", "Tapped"]
    assert seen[1]["action"] == "Damage"


def test_a_reconnecting_browser_resumes_from_its_last_event_id(harness) -> None:
    """This is what seq is for: no gap and no duplicate across a reconnect."""
    client, _, _, bus, _, _ = harness
    for _ in range(3):
        bus.publish(events.Navigated(target="RETRY"))
    bus.publish(events.RunStarted(run_id=7))

    with client.stream(
        "GET", "/api/events/stream", headers={"Last-Event-ID": "3"}
    ) as response:
        seen = read_events(response, 1)

    assert seen[0]["type"] == "RunStarted"
    assert seen[0]["seq"] == 4


def test_each_message_carries_its_seq_as_the_sse_id(harness) -> None:
    client, _, _, bus, _, _ = harness
    bus.publish(events.RunStarted(run_id=1))

    with client.stream("GET", "/api/events/stream") as response:
        ids = []
        for line in response.iter_lines():
            if line.startswith("id:"):
                ids.append(line[len("id:"):].strip())
                break

    assert ids == ["1"]


def test_a_junk_last_event_id_does_not_500(harness) -> None:
    client, _, _, bus, _, _ = harness
    bus.publish(events.RunStarted(run_id=1))

    with client.stream(
        "GET", "/api/events/stream", headers={"Last-Event-ID": "not-a-number"}
    ) as response:
        assert response.status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_web_api.py -k stream -q`
Expected: FAIL — 404 on `/api/events/stream`

- [ ] **Step 3: Add the endpoint**

In `web/app.py`, add these imports at the top:

```python
import asyncio
import json
from typing import AsyncIterator

from fastapi import Request
from fastapi.responses import StreamingResponse

from sinks.sse import to_payload
```

and this route inside `create_app`, after `run_events`:

```python
    @app.get("/api/events/stream")
    async def stream(request: Request) -> StreamingResponse:
        """Server-sent events, resumable through Last-Event-ID.

        Polling the ring rather than being pushed to: events arrive on the
        scan loop's thread and are consumed by the event loop, and a poll at
        four times a second against a 2s scan interval is imperceptible while
        needing no cross-thread wakeup at all.
        """
        cursor = _resume_point(request)

        async def pump() -> AsyncIterator[str]:
            nonlocal cursor
            idle = 0.0
            while not await request.is_disconnected():
                batch = sse.since(cursor)
                for event in batch:
                    cursor = event.seq
                    yield (
                        f"id: {event.seq}\n"
                        f"data: {json.dumps(to_payload(event))}\n\n"
                    )
                idle = 0.0 if batch else idle + config.SSE_POLL_SECONDS
                if idle >= config.SSE_HEARTBEAT_SECONDS:
                    idle = 0.0
                    yield ": ping\n\n"  # keeps an idle connection alive
                await asyncio.sleep(config.SSE_POLL_SECONDS)

        return StreamingResponse(
            pump(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    def _resume_point(request: Request) -> int:
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web_api.py -q`
Expected: PASS (12 tests)

- [ ] **Step 5: Run the whole suite and commit**

```bash
uv run pytest -q
git add web/app.py tests/test_web_api.py
git commit -m "feat: stream events to the browser over SSE with resume"
```

---

### Task 7: The dashboard page

**Files:**
- Create: `web/static/index.html`
- Modify: `web/app.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `GET /api/status`, `GET /api/runs`, `GET /api/runs/{id}/events`, `GET /api/unknown`, `GET /api/events/stream`
- Produces: `GET /` — the dashboard, `text/html`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_api.py`:

```python
def test_the_dashboard_is_served_at_the_root(harness) -> None:
    client, *_ = harness

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "EventSource" in response.text  # it is the live page, not a stub
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_web_api.py -k dashboard -q`
Expected: FAIL — 404 on `/`

- [ ] **Step 3: Write the page**

Create `web/static/index.html`:

```html
<!doctype html>
<meta charset="utf-8">
<title>The Tower bot</title>
<style>
  :root {
    --bg: #14161a; --panel: #1c1f26; --line: #2a2f3a; --text: #e6e8ee;
    --dim: #8b93a5; --ok: #4ade80; --warn: #fbbf24; --bad: #f87171;
    --accent: #60a5fa;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  header {
    display: flex; gap: 20px; align-items: center; flex-wrap: wrap;
    padding: 12px 16px; border-bottom: 1px solid var(--line); background: var(--panel);
    position: sticky; top: 0; z-index: 1;
  }
  header b { font-weight: 600; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--bad); display: inline-block; }
  .dot.live { background: var(--ok); }
  .stat { color: var(--dim); }
  .stat b { color: var(--text); margin-left: 6px; }
  main { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 12px; }
  section { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 12px; }
  section h2 { margin: 0 0 8px; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--dim); }
  #feed { height: 340px; overflow-y: auto; }
  #feed div { padding: 1px 0; white-space: pre-wrap; word-break: break-word; }
  .t-Tapped { color: var(--ok); }
  .t-Skipped { color: var(--dim); }
  .t-ScreenChanged, .t-Navigated { color: var(--accent); }
  .t-RunStarted, .t-RunEnded { color: var(--warn); }
  .t-BotError, .t-UnknownScreen { color: var(--bad); }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 3px 6px; border-bottom: 1px solid var(--line); }
  th { color: var(--dim); font-weight: 500; }
  tbody tr { cursor: pointer; }
  tbody tr:hover { background: #232833; }
  input, button { background: #232833; color: var(--text); border: 1px solid var(--line);
                  border-radius: 4px; padding: 4px 8px; font: inherit; }
  button { cursor: pointer; }
  #shots { display: flex; gap: 8px; flex-wrap: wrap; }
  #shots img { width: 90px; border: 1px solid var(--line); border-radius: 4px; }
  #error { color: var(--bad); }
  .wide { grid-column: 1 / -1; }
</style>

<header>
  <b>The Tower bot</b>
  <span><span class="dot" id="dot"></span> <span id="conn">connecting</span></span>
  <span class="stat">screen<b id="screen">-</b></span>
  <span class="stat">uptime<b id="uptime">-</b></span>
  <span class="stat">scans<b id="scans">0</b></span>
  <span class="stat">runs<b id="runs-done">0</b></span>
  <span class="stat">wallet<b id="wallet">-</b></span>
  <span class="stat">dropped<b id="dropped">0</b></span>
  <span id="error"></span>
</header>

<main>
  <section>
    <h2>Current run</h2>
    <div id="run">idle</div>
  </section>

  <section>
    <h2>Waves</h2>
    <canvas id="spark" width="480" height="60"></canvas>
  </section>

  <section class="wide">
    <h2>
      Events
      <input id="filter" placeholder="filter…" size="14">
      <button id="back" hidden>back to live</button>
      <span id="mode"></span>
    </h2>
    <div id="feed"></div>
  </section>

  <section>
    <h2>Run history</h2>
    <table>
      <thead><tr><th>#</th><th>wave</th><th>coins</th><th>tier</th><th>taps</th><th>length</th></tr></thead>
      <tbody id="history"></tbody>
    </table>
  </section>

  <section>
    <h2>Unknown screens</h2>
    <div id="shots"></div>
  </section>
</main>

<script>
  const $ = (id) => document.getElementById(id);
  const feed = $("feed");
  let historyMode = false;

  const clock = (ts) => new Date(ts * 1000).toTimeString().slice(0, 8);
  const money = (n) => (n === null || n === undefined ? "-" : "$" + n);

  function describe(e) {
    switch (e.type) {
      case "Tapped": return `TAP    ${e.action} (${e.x},${e.y}) score=${e.score.toFixed(3)} price=${money(e.price)}`;
      case "Skipped": return `SKIP   ${e.action} reason=${e.reason}${e.detail ? " " + e.detail : ""}`;
      case "ScreenChanged": return `SCREEN ${e.prev} -> ${e.screen ?? e.curr} (${e.confidence.toFixed(3)})`;
      case "ScanCompleted": return `SCAN   ${e.screen} ${Math.round(e.duration_ms)}ms wallet=${money(e.wallet)}`;
      case "RunStarted": return `RUN    #${e.run_id} started`;
      case "RunEnded": return `RUN    #${e.run_id} ${e.abandoned ? "abandoned" : "ended"} wave=${e.wave ?? "?"} coins=${e.coins ?? "?"}`;
      case "Navigated": return `NAV    ${e.target}`;
      case "UnknownScreen": return `UNKNWN best=${e.best_anchor} ${e.best_score.toFixed(3)}`;
      case "BotError": return `ERROR  ${e.message}`;
      default: return e.type;
    }
  }

  function show(e) {
    const row = document.createElement("div");
    row.className = "t-" + e.type;
    row.textContent = `${clock(e.ts)} ${describe(e)}`;
    row.dataset.text = row.textContent.toLowerCase();
    const term = $("filter").value.trim().toLowerCase();
    row.hidden = term !== "" && !row.dataset.text.includes(term);
    const pinned = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 20;
    feed.append(row);
    while (feed.childElementCount > 500) feed.firstElementChild.remove();
    if (pinned) feed.scrollTop = feed.scrollHeight;
  }

  $("filter").addEventListener("input", () => {
    const term = $("filter").value.trim().toLowerCase();
    for (const row of feed.children) {
      row.hidden = term !== "" && !row.dataset.text.includes(term);
    }
  });

  // --- live feed ----------------------------------------------------------
  // EventSource reconnects on its own and replays Last-Event-ID, so a dropped
  // connection costs nothing as long as the gap fits inside the server's ring.
  const source = new EventSource("/api/events/stream");
  source.onopen = () => { $("dot").classList.add("live"); $("conn").textContent = "live"; };
  source.onerror = () => { $("dot").classList.remove("live"); $("conn").textContent = "reconnecting"; };
  source.onmessage = (message) => {
    if (historyMode) return;
    show(JSON.parse(message.data));
  };

  // --- header + current run ----------------------------------------------
  async function refreshStatus() {
    const s = await (await fetch("/api/status")).json();
    $("screen").textContent = s.screen;
    $("uptime").textContent = Math.round(s.uptime) + "s";
    $("scans").textContent = s.scans;
    $("runs-done").textContent = s.runs_completed;
    $("wallet").textContent = money(s.wallet);
    $("dropped").textContent = s.dropped;
    $("error").textContent = s.last_error || "";
    if (s.run) {
      const taps = Object.entries(s.run.taps).map(([k, v]) => `${k} x${v}`).join(" · ") || "no taps yet";
      $("run").textContent = `#${s.run.id} · ${Math.round(s.run.elapsed)}s · ${taps}`;
    } else {
      $("run").textContent = "idle";
    }
  }

  // --- history + sparkline ------------------------------------------------
  function sparkline(waves) {
    const canvas = $("spark"), ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (waves.length < 2) return;
    const top = Math.max(...waves), pad = 4;
    const stepX = (canvas.width - pad * 2) / (waves.length - 1);
    const y = (w) => canvas.height - pad - (top ? (w / top) * (canvas.height - pad * 2) : 0);
    ctx.strokeStyle = "#60a5fa";
    ctx.lineWidth = 2;
    ctx.beginPath();
    waves.forEach((w, i) => (i ? ctx.lineTo(pad + i * stepX, y(w)) : ctx.moveTo(pad, y(w))));
    ctx.stroke();
  }

  async function refreshRuns() {
    const runs = await (await fetch("/api/runs?limit=30")).json();
    $("history").replaceChildren(...runs.map((run) => {
      const tr = document.createElement("tr");
      const length = run.ended_at ? Math.round(run.ended_at - run.started_at) + "s" : "live";
      for (const cell of [run.id, run.wave ?? "-", run.coins ?? "-", run.tier ?? "-", run.tap_count, length]) {
        const td = document.createElement("td");
        td.textContent = cell;
        tr.append(td);
      }
      tr.onclick = () => showRun(run.id);
      return tr;
    }));
    sparkline(runs.filter((r) => r.wave != null).map((r) => r.wave).reverse());
  }

  async function showRun(id) {
    historyMode = true;
    $("back").hidden = false;
    $("mode").textContent = ` — run #${id}`;
    const stored = await (await fetch(`/api/runs/${id}/events`)).json();
    feed.replaceChildren();
    // Stored rows are columns plus a detail blob; flatten them back into the
    // shape describe() takes, so one renderer serves live and history both.
    // Blob last: it holds no column's name except `detail` itself, whose
    // string value is the one the renderer wants back.
    for (const row of stored) show({ ...row, ...row.detail });
  }

  $("back").onclick = () => {
    historyMode = false;
    $("back").hidden = true;
    $("mode").textContent = "";
    feed.replaceChildren();
  };

  async function refreshShots() {
    const shots = await (await fetch("/api/unknown")).json();
    $("shots").replaceChildren(...shots.slice(0, 12).map((shot) => {
      const img = new Image();
      img.src = shot.url;
      img.title = new Date(shot.ts * 1000).toLocaleString();
      return img;
    }));
  }

  refreshStatus(); refreshRuns(); refreshShots();
  setInterval(refreshStatus, 2000);
  setInterval(refreshRuns, 15000);
  setInterval(refreshShots, 60000);
</script>
```

- [ ] **Step 4: Serve it**

In `web/app.py`, add `HTMLResponse` to the `fastapi.responses` import and this route inside `create_app`, before `/api/status`:

```python
    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        # Read per request rather than cached at import: editing the page and
        # hitting refresh is the whole development loop for it.
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_web_api.py -q`
Expected: PASS (13 tests)

- [ ] **Step 6: Run the whole suite and commit**

```bash
uv run pytest -q
git add web/static/index.html web/app.py tests/test_web_api.py
git commit -m "feat: add the dashboard page"
```

---

### Task 8: Wire it into the bot

**Files:**
- Modify: `tower_bot.py`
- Modify: `runs.py`
- Modify: `config.py`
- Modify: `pyproject.toml` (via `uv add`)
- Test: `tests/test_cli.py`, `tests/test_runs.py`

**Interfaces:**
- Consumes: everything from tasks 1-7
- Produces: `tower_bot.prepare_store(path: Path, retention_days: int = config.EVENT_RETENTION_DAYS) -> tuple[int, int]`, `tower_bot.serve_web(bot, app, *, host: str, port: int, interval: float, max_runs: int | None) -> None`, `RunTracker(start_id: int = 1)`, `TowerBot(..., first_run_id: int = 1)`, and the flags `--web`, `--web-host`, `--web-port`, `--db`, `--no-store`

- [ ] **Step 1: Add uvicorn**

```bash
uv add uvicorn
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_runs.py`:

```python
def test_run_ids_can_be_seeded_so_a_restart_does_not_reuse_them() -> None:
    """Run ids are the database's primary key. Restarting at 1 would
    overwrite the previous session's runs one by one."""
    from runs import RunTracker
    from screens import ScreenState

    tracker = RunTracker(start_id=12)

    started = tracker.transition(ScreenState.IN_RUN, now=0.0)

    assert started.run_id == 12
```

Append to `tests/test_cli.py`:

```python
def test_web_defaults_off() -> None:
    assert parse_args([]).web is False


def test_web_binds_loopback_by_default() -> None:
    """The dashboard serves session screenshots and has no auth."""
    args = parse_args(["--web"])
    assert args.web is True
    assert args.web_host == "127.0.0.1"


def test_the_store_is_on_by_default_and_can_be_turned_off() -> None:
    assert parse_args([]).store is True
    assert parse_args(["--no-store"]).store is False


def test_the_database_path_can_be_overridden() -> None:
    assert parse_args(["--db", "/tmp/other.db"]).db == "/tmp/other.db"


def test_prepare_store_seeds_both_counters_and_prunes(tmp_path) -> None:
    """A restart must continue the sequence, not collide with it."""
    import time

    import db
    from tower_bot import prepare_store

    path = tmp_path / "bot.db"
    conn = db.connect(path)
    db.start_run(conn, 5, started_at=1.0)
    db.insert_event(conn, {
        "seq": 41, "run_id": 5, "ts": time.time(), "type": "Navigated",
        "screen": None, "action": None, "reason": None, "score": None,
        "price": None, "wallet": None, "detail": None,
    })
    db.insert_event(conn, {
        "seq": 42, "run_id": 5, "ts": time.time() - 90 * 86400, "type": "Navigated",
        "screen": None, "action": None, "reason": None, "score": None,
        "price": None, "wallet": None, "detail": None,
    })
    conn.close()

    seed_seq, last_run = prepare_store(path)

    assert (seed_seq, last_run) == (42, 5)
    with db.reader(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_a_seeded_bus_does_not_reissue_a_stored_seq() -> None:
    import events

    bus = events.EventBus(start_seq=42)

    assert bus.publish(events.Navigated(target="RETRY")).seq == 43


def test_serve_web_runs_the_scan_loop_beside_the_server(monkeypatch) -> None:
    """uvicorn owns the main thread; the loop must run and then be stopped."""
    import threading

    import tower_bot

    class FakeBot:
        def __init__(self) -> None:
            self.scanned = threading.Event()
            self.stopped = False

        def run_forever(self, interval: float, max_runs: int | None) -> None:
            self.scanned.set()

        def stop(self) -> None:
            self.stopped = True

    bot = FakeBot()
    seen: dict[str, object] = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: seen.update(kwargs))

    tower_bot.serve_web(
        bot, object(), host="127.0.0.1", port=8123, interval=0.01, max_runs=None
    )

    assert bot.scanned.wait(timeout=2)
    assert bot.stopped is True
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 8123)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py tests/test_runs.py -q`
Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'web'`, `TypeError: RunTracker() takes no keyword arguments`

- [ ] **Step 4: Seed the run tracker**

In `runs.py`, replace `RunTracker.__init__` with:

```python
    def __init__(self, start_id: int = 1) -> None:
        # Seeded from the database at startup. Run ids are the runs table's
        # primary key: restarting at 1 would overwrite the previous session's
        # runs one by one, exactly as an unseeded seq would collide on the
        # events table.
        self._next_id = start_id
```

and document it in the module docstring by appending this paragraph:

```
Run ids are handed out from `start_id`, which the caller seeds from the
database so a restart continues the numbering instead of colliding with it.
```

- [ ] **Step 5: Add the web config**

Append to `config.py`:

```python
# --- Web dashboard --------------------------------------------------------
# SECURITY: loopback only, and there is no auth. The dashboard serves
# screenshots of a live session and the full event history of this machine.
# Binding 0.0.0.0 puts both on the local network in the clear - do not change
# this without putting real authentication in front of it first.
WEB_HOST: str = "127.0.0.1"
WEB_PORT: int = 8765
```

- [ ] **Step 6: Wire up `tower_bot.py`**

Replace the sink imports at the top of `tower_bot.py`:

```python
from sinks.log import LogSink
from sinks.sse import SseSink
from sinks.state import BotState, StateSink
from sinks.store import StoreSink
from sinks.tui import TuiSink
```

Add `first_run_id` to `TowerBot.__init__`'s signature (after `reader`):

```python
        first_run_id: int = 1,
```

and replace `self.runs = RunTracker()` with:

```python
        self.runs = RunTracker(first_run_id)
```

Add these flags in `parse_args`, before the `return`:

```python
    parser.add_argument(
        "--web", action="store_true",
        help="serve the dashboard while the bot runs (default: off)",
    )
    parser.add_argument(
        "--web-host", default=config.WEB_HOST,
        help="dashboard bind address - loopback by default, and there is no auth",
    )
    parser.add_argument("--web-port", type=int, default=config.WEB_PORT)
    parser.add_argument(
        "--db", default=str(config.DB_PATH), help="SQLite file for the event log"
    )
    parser.add_argument(
        "--no-store", dest="store", action="store_false", default=True,
        help="do not persist events to SQLite",
    )
```

Add these two functions above `main`:

```python
def prepare_store(
    path: Path, retention_days: int = config.EVENT_RETENTION_DAYS
) -> tuple[int, int]:
    """Create the database, prune it, and report what to seed the counters to.

    Returns `(max_seq, max_run_id)`. Both are in-process counters that would
    otherwise restart at zero on every launch: seq would collide with stored
    rows on the events primary key and break SSE resume across a restart, and
    run ids would overwrite the previous session's runs one at a time.
    """
    conn = db.connect(path)
    try:
        removed = db.prune_events(conn, retention_days)
        if removed:
            logger.info("Pruned %d events older than %d days", removed, retention_days)
        return db.max_seq(conn), db.max_run_id(conn)
    finally:
        conn.close()


def serve_web(
    bot: TowerBot,
    app: object,
    *,
    host: str,
    port: int,
    interval: float,
    max_runs: int | None,
) -> None:
    """Run the server on this thread and the scan loop beside it.

    This way round on purpose. uvicorn installs its own SIGINT/SIGTERM
    handlers and can only do that from the main thread, so it gets the main
    thread and the scan loop gets a worker. They genuinely run in parallel
    despite the GIL: cv2.matchTemplate releases it for the duration of the
    match, which is where a scan spends nearly all of its time.
    """
    import uvicorn

    worker = threading.Thread(
        target=bot.run_forever,
        kwargs={"interval": interval, "max_runs": max_runs},
        name="scan-loop",
        daemon=True,
    )
    worker.start()
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        # uvicorn returns on Ctrl+C; the loop must be told, or the process
        # hangs around scanning with nothing watching.
        bot.stop()
        worker.join(timeout=interval + 2.0)
```

Add `import threading` and `import db` to the imports at the top of the file.

Replace `main` from the `bus = events.EventBus()` line to the end of the function with:

```python
    db_path = Path(args.db)
    seed_seq, last_run = prepare_store(db_path) if args.store else (0, 0)

    bus = events.EventBus(start_seq=seed_seq)
    state = BotState()
    sinks: list = [TuiSink(state=state) if args.tui else LogSink()]
    if args.store:
        sinks.append(StoreSink(db_path))
    if args.web and not args.tui:
        # Under --tui the panel's sink already feeds the shared state.
        sinks.append(StateSink(state))
    sse = SseSink() if args.web else None

    for sink in sinks:
        bus.subscribe(sink)
    if sse is not None:
        bus.subscribe(sse)  # no thread to start: it appends and returns

    # Everything from start() onwards is inside the try: anything raising
    # between starting the consumer threads and the loop would otherwise
    # leave them running and, under --tui, leave rich's Live holding the
    # terminal.
    try:
        for sink in sinks:
            sink.start()

        try:
            frame = capture_screen(device)
        except Exception as exc:  # noqa: BLE001 - a bad guard frame must not abort startup
            logger.warning("Could not capture a frame to verify resolution: %s", exc)
        else:
            height, width = frame.shape[:2]
            if (width, height) != config.EXPECTED_RESOLUTION:
                logger.warning(
                    "Emulator is %dx%d but templates were captured at %dx%d. "
                    "Template matching is not scale-invariant - re-capture them.",
                    width, height, *config.EXPECTED_RESOLUTION,
                )

        bot = TowerBot(
            device=device,
            templates=vision.TemplateCache(config.TEMPLATE_DIR),
            bus=bus,
            affordability_check=build_affordability(args.affordability),
            auto_navigate=args.auto_navigate,
            first_run_id=last_run + 1,
        )

        if args.once:
            # A single scan never settles the debounced tracker (it needs
            # SCREEN_CONFIRMATIONS consecutive identical readings), so a
            # lone run_once() would always report UNKNOWN even when the
            # game is clearly on GAME_OVER at 0.998. Scan enough times to
            # settle so --once actually names the real screen.
            install_signal_handlers(bot)
            for _ in range(config.SCREEN_CONFIRMATIONS):
                bot.run_once()
        elif args.web:
            from web.app import create_app

            app = create_app(state=state, sse=sse, bus=bus, db_path=db_path)
            logger.info("Dashboard on http://%s:%d", args.web_host, args.web_port)
            # No signal handlers of ours here: uvicorn installs its own and
            # would overwrite them anyway.
            serve_web(
                bot, app, host=args.web_host, port=args.web_port,
                interval=args.interval, max_runs=args.max_runs,
            )
        else:
            install_signal_handlers(bot)
            bot.run_forever(interval=args.interval, max_runs=args.max_runs)
    finally:
        for sink in sinks:
            sink.close()
    return 0
```

and lift the signal registration that used to be inline into a function above `main`:

```python
def install_signal_handlers(bot: TowerBot) -> None:
    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s - shutting down after this scan.", signum)
        bot.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py tests/test_runs.py -q`
Expected: PASS

- [ ] **Step 8: Run the whole suite and commit**

```bash
uv run pytest -q
git add tower_bot.py runs.py config.py tests/test_cli.py tests/test_runs.py pyproject.toml uv.lock
git commit -m "feat: run the dashboard and the event store alongside the scan loop"
```

- [ ] **Step 9: Smoke-test it against the emulator**

With the emulator running:

```bash
uv run tower_bot.py --web --auto-navigate
```

Open <http://127.0.0.1:8765> and confirm, in this order:

1. The header dot turns green and reads `live` within a second or two.
2. `screen` tracks the game — start a run and watch it move to `IN_RUN`.
3. Events appear in the feed as they happen; the filter box narrows them.
4. The current-run panel counts taps while a run is live and returns to `idle` after a death.
5. The run history table gains a row per completed run, with wave/coins/tier filled in.
6. Clicking a history row replays that run's stored events; `back to live` returns to the stream.
7. Stop the bot with Ctrl+C — the process exits, and the row for the last run is still there when you restart with `--web`.
8. Restart and confirm the new run's id continues from the last one rather than restarting at 1.

Record anything that misbehaves in the spec's section 13 rather than leaving it in the terminal scrollback.

---

## Done when

- `uv run pytest -q` passes with no emulator attached and no browser.
- `uv run tower_bot.py --web` serves a dashboard on `127.0.0.1:8765` that shows the live screen, the current run, and a filterable event feed.
- Killing and restarting the bot continues both the `seq` and the run-id sequences instead of colliding with stored rows.
- `ScanCompleted` appears in the live feed and the TUI but never in the events table; the run row carries `scan_count` instead.
- A completed run's wave, coins, tier, scan count and tap count are all queryable from `/api/runs` after the process exits.
- The scan loop keeps scanning at its interval while the dashboard is open — the bus invariant survives a browser.
- `--no-store` runs the bot with no database at all, and `--web` still works alongside `--tui`.

## Follow-on plans

- **Plan 4** — phase 5: README rewrite, including the brightness section plan 2 superseded, the note that templates must be cut from a lit in-run frame, and the new `--web` / `--db` flags.
