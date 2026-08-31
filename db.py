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


def close_abandoned_runs(conn: sqlite3.Connection) -> int:
    """Close out runs a killed process never finished. Returns how many.

    `RunEnded` never fires for a kill (only for the tracked run/game-over
    lifecycle), so the row keeps `ended_at IS NULL` - which is exactly what
    the dashboard reads as "live" - forever, and `runs` is never pruned by
    age the way `events` is. `started_at` stands in for `ended_at` rather
    than "now": no real duration was ever observed, and backdating to the
    run's own start is honest about that instead of inventing one.
    """
    cursor = conn.execute(
        "UPDATE runs SET ended_at = started_at, abandoned = 1 "
        "WHERE ended_at IS NULL"
    )
    conn.commit()
    return cursor.rowcount


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
