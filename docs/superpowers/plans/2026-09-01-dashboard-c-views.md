# Dashboard Rewrite, Plan C — Live Device Screen, Drill-down, Stats and Errors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show what the bot is actually looking at, what any single run did, what every run adds up to, and everything that went wrong.

**Architecture:** A lock-protected `FrameBuffer` holds the last captured frame plus that scan's match boxes, encoded to JPEG once per frame and streamed as MJPEG so a plain `<img>` renders it with no JavaScript. Overlay boxes travel as JSON on `/api/status` and are drawn in React, keeping the image a plain cacheable JPEG and scaling a CSS problem. History gets four new SQL aggregates in `db.py` behind two endpoints, and three new pages in the Next.js app.

**Tech Stack:** Python 3.12, OpenCV (already a dependency), FastAPI, pytest. UI: Next.js/React/TypeScript from Plan A, plus Recharts.

**Spec:** `docs/superpowers/specs/2026-09-01-tower-bot-dashboard-ui-design.md` (§5, §6)

**Depends on:** Plan A. Independent of Plan B — it touches no control code — but if both are done, do B first so the sidebar's Control link is not the only dead one.

## Global Constraints

- Python >= 3.12. Type hints on every function. `from __future__ import annotations` at the top of every Python module.
- Python dependencies via `uv add`; node dependencies via `npm install` inside `web/ui/`. Never hand-edit `pyproject.toml`.
- No route writes to the database. `db.reader()` keeps opening `mode=ro`.
- Aggregates are **SQL**, not Python loops over `list_runs()`. The `events_ts_idx` and `events_run_idx` indexes already exist; use them.
- Any endpoint that holds a connection open must end itself on the shared `stop` Event, exactly as `event_stream()` does. This is the hang the SSE stream already hit once — do not re-introduce it with MJPEG.
- **Load the `dataviz` skill before writing the first chart** (Task 6).
- After any UI change, `npm run build` in `web/ui` and commit `web/static/`.
- Commit after every task.

## File Structure

| Path | Responsibility |
|---|---|
| `frames.py` | `FrameBuffer`: last frame, its boxes, cached JPEG |
| `tower_bot.py` | modify: feed the buffer from the scan loop |
| `web/app.py` | modify: `/api/frame`, `/api/stats`, `/api/errors`; boxes on `/api/status` |
| `db.py` | modify: `run_stats`, `taps_by_action`, `screen_histogram`, `error_log` |
| `web/ui/components/DeviceView.tsx` | the MJPEG image plus its overlay |
| `web/ui/app/runs/page.tsx` | run list and `?id=` drill-down |
| `web/ui/app/stats/page.tsx` | aggregate charts |
| `web/ui/app/errors/page.tsx` | errors and unknown screens |
| `tests/test_frames.py` | encode-once, boxes, thread safety |
| `tests/test_stats_api.py` | the aggregate SQL and its routes |

---

### Task 1: The frame buffer

**Files:**
- Create: `frames.py`
- Test: `tests/test_frames.py`

**Interfaces:**
- Produces: `FrameBuffer` with `publish(frame)`, `add_box(box)`, `latest() -> tuple[int, bytes] | None`, `boxes() -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_frames.py`:

```python
"""The frame buffer: one encode per frame, however many tabs are watching."""

from __future__ import annotations

import threading

import numpy as np

from frames import FrameBuffer


def a_frame(value: int = 128) -> np.ndarray:
    return np.full((80, 60, 3), value, dtype=np.uint8)


def test_empty_buffer_has_nothing_to_serve() -> None:
    assert FrameBuffer().latest() is None


def test_publish_makes_a_jpeg_available() -> None:
    buffer = FrameBuffer()
    buffer.publish(a_frame())
    result = buffer.latest()
    assert result is not None
    number, payload = result
    assert number == 1
    assert payload[:2] == b"\xff\xd8"  # JPEG SOI


def test_encoding_happens_once_per_frame_not_once_per_reader() -> None:
    """Ten open tabs must cost one imencode per scan, not ten."""
    buffer = FrameBuffer()
    buffer.publish(a_frame())
    first = buffer.latest()
    second = buffer.latest()
    assert first is not None and second is not None
    # Same object identity: the second read returned the cache, not a re-encode.
    assert first[1] is second[1]


def test_a_new_frame_invalidates_the_cache() -> None:
    buffer = FrameBuffer()
    buffer.publish(a_frame(0))
    first = buffer.latest()
    buffer.publish(a_frame(255))
    second = buffer.latest()
    assert first is not None and second is not None
    assert second[0] == first[0] + 1
    assert second[1] is not first[1]


def test_publish_clears_the_previous_frame_boxes() -> None:
    """Boxes belong to the frame they were matched against.

    Carrying them over would draw last scan's matches on this scan's picture -
    which is precisely the kind of lie a debugging view must never tell.
    """
    buffer = FrameBuffer()
    buffer.publish(a_frame())
    buffer.add_box({"name": "Damage", "x": 1, "y": 2, "w": 3, "h": 4, "score": 0.99, "tapped": True})
    assert len(buffer.boxes()) == 1
    buffer.publish(a_frame())
    assert buffer.boxes() == []


def test_boxes_are_detached_copies() -> None:
    buffer = FrameBuffer()
    buffer.publish(a_frame())
    buffer.add_box({"name": "Damage", "x": 1, "y": 2, "w": 3, "h": 4, "score": 0.9, "tapped": False})
    got = buffer.boxes()
    got.clear()
    assert len(buffer.boxes()) == 1


def test_concurrent_publish_and_read_do_not_tear() -> None:
    buffer = FrameBuffer()
    errors: list[Exception] = []

    def writer() -> None:
        try:
            for value in range(100):
                buffer.publish(a_frame(value % 256))
        except Exception as exc:  # noqa: BLE001 - the test is what it catches
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(100):
                result = buffer.latest()
                if result is not None:
                    assert result[1][:2] == b"\xff\xd8"
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_frames.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'frames'`.

- [ ] **Step 3: Write `frames.py`**

```python
"""The last frame the bot looked at, ready to serve.

Encoded lazily and cached against a frame number, so N watching tabs cost one
cv2.imencode per scan rather than N. The encode happens on whichever thread
asks first - a web thread, never the scan loop - and cv2 releases the GIL for
the duration, so it does not stall a scan.

Boxes travel separately, as JSON on /api/status, rather than being drawn into
the pixels: it keeps the JPEG a plain cacheable image, lets the overlay be
toggled off, and makes scaling a CSS problem instead of a coordinate-mapping
problem in two languages.
"""

from __future__ import annotations

import threading
from typing import Any

import cv2
import numpy as np

# High enough to read a wave counter, low enough that a frame every couple of
# seconds is nothing on loopback.
JPEG_QUALITY = 80


class FrameBuffer:
    def __init__(self, quality: int = JPEG_QUALITY) -> None:
        self._lock = threading.Lock()
        self._quality = quality
        self._frame: np.ndarray | None = None
        self._number = 0
        self._boxes: list[dict[str, Any]] = []
        self._encoded: bytes | None = None
        self._encoded_number = 0

    def publish(self, frame: np.ndarray) -> None:
        """Hand the buffer this scan's frame. Clears the previous scan's boxes."""
        with self._lock:
            self._frame = frame
            self._number += 1
            self._boxes = []
            self._encoded = None

    def add_box(self, box: dict[str, Any]) -> None:
        """Record one match against the current frame."""
        with self._lock:
            self._boxes.append(dict(box))

    def boxes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(box) for box in self._boxes]

    def latest(self) -> tuple[int, bytes] | None:
        """The current frame number and its JPEG bytes, or None if never fed.

        Returns the identical bytes object on repeat calls for one frame, which
        is what makes "one encode per frame" observable in a test and true in
        production.
        """
        with self._lock:
            if self._frame is None:
                return None
            if self._encoded is not None and self._encoded_number == self._number:
                return self._number, self._encoded
            frame = self._frame
            number = self._number

        # Encode outside the lock: it is the slow part, and holding the lock
        # through it would make a scan's publish() wait on a browser's read.
        ok, buffer = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._quality]
        )
        if not ok:
            return None
        payload = buffer.tobytes()

        with self._lock:
            # Another publish may have landed while we encoded; only cache if
            # this is still the current frame.
            if number == self._number:
                self._encoded = payload
                self._encoded_number = number
            return number, payload
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_frames.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add frames.py tests/test_frames.py
git commit -m "feat: buffer the last frame for the dashboard"
```

---

### Task 2: Feed the buffer from the scan loop

**Files:**
- Modify: `tower_bot.py` — `TowerBot.__init__`, `refresh_screen`, `find_and_click_image`

**Interfaces:**
- Consumes: `FrameBuffer` from Task 1.
- Produces: `TowerBot(..., frames: FrameBuffer | None = None)`; `bot.frames` is fed every scan.

- [ ] **Step 1: Take the buffer in the constructor**

Add to `TowerBot.__init__`'s parameters:

```python
        frames: FrameBuffer | None = None,
```

and to the body:

```python
        # Optional: --tui and --once have nobody to show a frame to, and every
        # test predating this constructs a bot without one.
        self.frames = frames
```

Import at the top of `tower_bot.py`:

```python
from frames import FrameBuffer
```

- [ ] **Step 2: Publish every captured frame**

In `refresh_screen`:

```python
    def refresh_screen(self) -> Image:
        """Capture a fresh frame and keep it as the current screen."""
        self._screen = capture_screen(self.device)
        if self.frames is not None:
            self.frames.publish(self._screen)
        return self._screen
```

- [ ] **Step 3: Record the match boxes**

In `find_and_click_image`, immediately after the `if match is None: return False` guard:

```python
        if self.frames is not None:
            # Recorded whether or not the tap happens, because "matched but
            # rejected" is exactly what you open the device view to see.
            height, width = template.shape[:2]
            self.frames.add_box({
                "name": action.name,
                "x": int(match.top_left[0]),
                "y": int(match.top_left[1]),
                "w": int(width),
                "h": int(height),
                "score": float(match.score),
                "tapped": False,
            })
```

and immediately after the `self.bus.publish(events.Tapped(...))` call at the end:

```python
        if self.frames is not None:
            self.frames.mark_tapped(action.name)
```

Add that method to `frames.py`:

```python
    def mark_tapped(self, name: str) -> None:
        """Flag a recorded box as the one that was actually tapped."""
        with self._lock:
            for box in self._boxes:
                if box["name"] == name:
                    box["tapped"] = True
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green — every existing test builds a bot with `frames=None` and takes the untouched path.

- [ ] **Step 5: Commit**

```bash
git add tower_bot.py frames.py
git commit -m "feat: record each scan's frame and match boxes"
```

---

### Task 3: Serve the frame

**Files:**
- Modify: `web/app.py`, `tower_bot.py` (`main()`)
- Test: `tests/test_frame_api.py`

**Interfaces:**
- Consumes: `FrameBuffer`.
- Produces: `GET /api/frame` (MJPEG), `GET /api/frame.jpg` (one still), and `boxes` on `/api/status`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_frame_api.py`:

```python
"""The device view's endpoints."""

from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

import config
from events import EventBus
from frames import FrameBuffer
from sinks.sse import SseSink
from sinks.state import BotState
from web.app import create_app, frame_stream


def a_frame() -> np.ndarray:
    return np.full((80, 60, 3), 128, dtype=np.uint8)


@pytest.fixture
def wired() -> tuple[TestClient, FrameBuffer, threading.Event]:
    frames = FrameBuffer()
    stop = threading.Event()
    app = create_app(
        state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None,
        unknown_dir=config.UNKNOWN_DIR, stop=stop, frames=frames,
    )
    return TestClient(app), frames, stop


def test_still_is_404_before_the_first_scan(wired) -> None:
    client, _, _ = wired
    assert client.get("/api/frame.jpg").status_code == 404


def test_still_serves_a_jpeg(wired) -> None:
    client, frames, _ = wired
    frames.publish(a_frame())
    response = client.get("/api/frame.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"


def test_status_carries_the_overlay_boxes(wired) -> None:
    client, frames, _ = wired
    frames.publish(a_frame())
    frames.add_box({"name": "Damage", "x": 1, "y": 2, "w": 3, "h": 4, "score": 0.9, "tapped": True})
    body = client.get("/api/status").json()
    assert body["boxes"][0]["name"] == "Damage"
    assert body["frame_size"] == {"width": 60, "height": 80}


def test_the_stream_ends_when_stop_is_set(wired) -> None:
    """The hang the SSE stream already hit once, not re-introduced.

    An in-flight response uvicorn's graceful shutdown waits on forever is
    exactly what `stop` exists to prevent - and a held-open <img> never
    disconnects on its own just because the bot ended.
    """
    _, frames, stop = wired
    frames.publish(a_frame())

    async def drain() -> int:
        chunks = 0
        stop.set()
        async for _ in frame_stream(frames, lambda: _never(), stop=stop, poll=0.01):
            chunks += 1
        return chunks

    async def _never() -> bool:
        return False

    assert asyncio.run(drain()) == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_frame_api.py -v`
Expected: FAIL — `cannot import name 'frame_stream'`.

- [ ] **Step 3: Add the generator and the routes**

In `web/app.py`, add above `create_app`:

```python
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
```

Extend `create_app`'s signature with `frames: FrameBuffer | None = None`, and add these routes before the static mount:

```python
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
```

Extend the existing `status()` route, after `payload["dropped"] = bus.dropped`:

```python
        # Overlay geometry rides along with the status poll rather than getting
        # its own endpoint: it changes exactly as often as the status does, and
        # a second poll would buy nothing.
        payload["boxes"] = frames.boxes() if frames is not None else []
        payload["frame_size"] = None
        if frames is not None:
            size = frames.size()
            if size is not None:
                payload["frame_size"] = {"width": size[0], "height": size[1]}
```

Add `size()` to `frames.py`:

```python
    def size(self) -> tuple[int, int] | None:
        """(width, height) of the current frame, for the overlay's coordinates."""
        with self._lock:
            if self._frame is None:
                return None
            height, width = self._frame.shape[:2]
            return int(width), int(height)
```

Extend the imports in `web/app.py`:

```python
from fastapi import Response
from frames import FrameBuffer
```

- [ ] **Step 4: Pass the buffer through `main()`**

In `main()`, create the buffer alongside the SSE sink and hand it to both the bot and the app:

```python
    frames = FrameBuffer() if args.web else None
```

Add `frames=frames` to the `TowerBot(...)` call and to the `create_app(...)` call.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add web/app.py frames.py tower_bot.py tests/test_frame_api.py
git commit -m "feat: stream the device screen to the dashboard"
```

---

### Task 4: The device view

**Files:**
- Create: `web/ui/components/DeviceView.tsx`
- Modify: `web/ui/lib/types.ts`, `web/ui/app/page.tsx`

- [ ] **Step 1: Extend the types**

Append to `web/ui/lib/types.ts`, and add the two fields to `StatusPayload`:

```ts
export interface MatchBox {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
  score: number;
  tapped: boolean;
}
```

```ts
  boxes: MatchBox[];
  frame_size: { width: number; height: number } | null;
```

- [ ] **Step 2: Write the component**

Create `web/ui/components/DeviceView.tsx`:

```tsx
"use client";

import { useState } from "react";
import type { MatchBox } from "@/lib/types";

export function DeviceView({
  boxes,
  size,
}: {
  boxes: MatchBox[];
  size: { width: number; height: number } | null;
}) {
  const [overlay, setOverlay] = useState(true);

  return (
    <div className="flex flex-col gap-2">
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <input type="checkbox" checked={overlay} onChange={(e) => setOverlay(e.target.checked)} />
        show matches
      </label>

      {/* The boxes are absolutely positioned in percentages of the frame's own
          pixel dimensions, so the image can be any size on screen and the
          overlay follows it - no coordinate maths in two languages. */}
      <div className="relative w-full max-w-xs overflow-hidden rounded-lg border">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/api/frame" alt="device screen" className="block w-full" />
        {overlay && size
          ? boxes.map((box) => (
              <div
                key={`${box.name}-${box.x}-${box.y}`}
                title={`${box.name} ${box.score.toFixed(3)}`}
                className={`absolute border-2 ${box.tapped ? "border-emerald-400" : "border-amber-400"}`}
                style={{
                  left: `${(box.x / size.width) * 100}%`,
                  top: `${(box.y / size.height) * 100}%`,
                  width: `${(box.w / size.width) * 100}%`,
                  height: `${(box.h / size.height) * 100}%`,
                }}
              />
            ))
          : null}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Put it on the Live page**

In `web/ui/app/page.tsx`, import it and replace the "Current run" section's grid so the device sits beside the run summary:

```tsx
        <section className="rounded-lg border p-3">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Device</h2>
          <DeviceView boxes={status?.boxes ?? []} size={status?.frame_size ?? null} />
        </section>
```

- [ ] **Step 4: Build and check by hand**

```bash
cd web/ui && npm test && npm run build && cd ../.. && uv run pytest -q
uv run tower_bot.py --web --no-store
```
The device panel must show the live emulator, with amber boxes on matched upgrades and green on the one that was tapped. Toggling the checkbox must hide them. Closing the tab must not stop the bot; Ctrl+C must still exit cleanly with the tab open.

- [ ] **Step 5: Commit**

```bash
git add web/ui web/static
git commit -m "feat: show the live device screen with its match overlay"
```

---

### Task 5: The aggregate queries

**Files:**
- Modify: `db.py`, `web/app.py`
- Test: `tests/test_stats_api.py`

**Interfaces:**
- Produces: `db.run_stats(conn, limit)`, `db.taps_by_action(conn)`, `db.screen_histogram(conn)`, `db.error_log(conn, limit)`; `GET /api/stats`, `GET /api/errors`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stats_api.py`:

```python
"""Aggregates, in SQL. Read the existing tests/test_db.py for its fixtures
and reuse whatever it already uses to build a populated database."""

from __future__ import annotations

import sqlite3

import pytest

import db


@pytest.fixture
def populated(tmp_path):
    path = tmp_path / "events.db"
    conn = db.connect(path)
    conn.executescript(
        """
        INSERT INTO runs (id, started_at, ended_at, wave, coins, tier, scan_count, tap_count)
        VALUES (1, 100, 200, 50, 1000, 3, 40, 6),
               (2, 300, 460, 70, 2000, 4, 70, 9);

        INSERT INTO events (seq, run_id, ts, type, screen, action, reason)
        VALUES (1, 1, 110, 'Tapped',   'IN_RUN', 'Damage',      NULL),
               (2, 1, 120, 'Tapped',   'IN_RUN', 'Damage',      NULL),
               (3, 2, 310, 'Tapped',   'IN_RUN', 'Attack Speed',NULL),
               (4, 2, 320, 'BotError', NULL,     NULL,          NULL),
               (5, 2, 330, 'ScanCompleted', 'IN_RUN', NULL,     NULL),
               (6, 2, 340, 'ScanCompleted', 'MENU',   NULL,     NULL);
        """
    )
    conn.commit()
    conn.close()
    return path


def test_run_stats_returns_finished_runs_with_their_duration(populated) -> None:
    with db.reader(populated) as conn:
        rows = db.run_stats(conn, limit=10)
    assert [row["id"] for row in rows] == [1, 2]
    assert rows[0]["duration"] == 100
    assert rows[1]["wave"] == 70


def test_taps_by_action_counts_only_taps(populated) -> None:
    with db.reader(populated) as conn:
        rows = db.taps_by_action(conn)
    counts = {row["action"]: row["count"] for row in rows}
    assert counts == {"Damage": 2, "Attack Speed": 1}


def test_screen_histogram_counts_every_event_that_names_a_screen(populated) -> None:
    # Four IN_RUN rows (seq 1, 2, 3, 5) and one MENU (seq 6); seq 4 names no
    # screen and must not be counted.
    with db.reader(populated) as conn:
        rows = db.screen_histogram(conn)
    counts = {row["screen"]: row["count"] for row in rows}
    assert counts == {"IN_RUN": 4, "MENU": 1}


def test_error_log_returns_newest_first(populated) -> None:
    with db.reader(populated) as conn:
        rows = db.error_log(conn, limit=10)
    assert len(rows) == 1
    assert rows[0]["type"] == "BotError"


def test_the_reader_still_cannot_write(populated) -> None:
    """The one guarantee none of this may weaken."""
    with db.reader(populated) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM runs")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_stats_api.py -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'run_stats'`.

- [ ] **Step 3: Write the queries**

Append to `db.py`:

```python
def run_stats(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    """Finished runs, oldest first, with their duration precomputed.

    Oldest first because every consumer is a time series; reversing a DESC
    result in the browser is work the database already knows how to avoid.
    """
    rows = conn.execute(
        """SELECT id, started_at, ended_at, wave, coins, tier, tap_count,
                  scan_count, ended_at - started_at AS duration
             FROM runs
            WHERE ended_at IS NOT NULL
            ORDER BY id DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def taps_by_action(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Which upgrades actually get bought."""
    rows = conn.execute(
        """SELECT action, COUNT(*) AS count
             FROM events
            WHERE type = 'Tapped' AND action IS NOT NULL
            GROUP BY action
            ORDER BY count DESC"""
    ).fetchall()
    return [dict(row) for row in rows]


def screen_histogram(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Where the bot spends its scans.

    ScanCompleted is never stored (the run row carries scan_count instead), so
    this counts the screens on every event that names one - which is the same
    question and the only one this table can answer.
    """
    rows = conn.execute(
        """SELECT screen, COUNT(*) AS count
             FROM events
            WHERE screen IS NOT NULL
            GROUP BY screen
            ORDER BY count DESC"""
    ).fetchall()
    return [dict(row) for row in rows]


def error_log(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """BotError rows, newest first, with their tracebacks decoded."""
    rows = conn.execute(
        """SELECT * FROM events
            WHERE type = 'BotError'
            ORDER BY seq DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_decode(row) for row in rows]
```

The fixture inserts `ScanCompleted` rows by hand, which the real store sink never writes — the run row carries `scan_count` instead. That is deliberate: it exercises the SQL's grouping without pretending the production table contains rows it does not. `screen_histogram` therefore answers "events by screen", not "scans by screen", and the chart in Task 6 is labelled accordingly.

- [ ] **Step 4: Add the routes**

In `web/app.py`, before the static mount:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add db.py web/app.py tests/test_stats_api.py
git commit -m "feat: aggregate stored runs and errors in SQL"
```

---

### Task 6: The Runs, Stats and Errors pages

**Files:**
- Create: `web/ui/app/runs/page.tsx`, `web/ui/app/stats/page.tsx`, `web/ui/app/errors/page.tsx`
- Modify: `web/ui/lib/api.ts`, `web/ui/lib/types.ts`

- [ ] **Step 1: Load the dataviz skill**

Before writing any chart, invoke the `dataviz` skill. The spec requires it, and the palette and axis rules it carries are what keep three charts on two pages looking like one system.

- [ ] **Step 2: Install Recharts**

```bash
cd web/ui && npm install recharts
```

- [ ] **Step 3: Extend the client**

Append to `web/ui/lib/types.ts`:

```ts
export interface RunStat {
  id: number;
  started_at: number;
  ended_at: number;
  wave: number | null;
  coins: number | null;
  tier: number | null;
  tap_count: number;
  scan_count: number;
  duration: number;
}

export interface StatsPayload {
  runs: RunStat[];
  taps: { action: string; count: number }[];
  screens: { screen: string; count: number }[];
}
```

Add `StatsPayload` to the existing `import type { ... } from "./types";` line at the top of `web/ui/lib/api.ts`, then append:

```ts
export const fetchStats = () => getJson<StatsPayload>("/api/stats");
export const fetchErrors = (limit = 100) => getJson<StoredEvent[]>(`/api/errors?limit=${limit}`);
```

- [ ] **Step 4: Write the Runs page**

Create `web/ui/app/runs/page.tsx`. The `Suspense` wrapper is **required**, not stylistic: `useSearchParams` in a statically exported page must sit inside one or the build fails.

```tsx
"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { EventFeed } from "@/components/EventFeed";
import { RunTable } from "@/components/RunTable";
import { fetchRunEvents, fetchRuns } from "@/lib/api";
import type { BotEvent, RunRow, StoredEvent } from "@/lib/types";

function Runs() {
  const router = useRouter();
  const params = useSearchParams();
  // Static export cannot prerender /runs/[id] - the ids do not exist at build
  // time - so the id rides in the query string instead. Same deep link, one
  // exported route, no SPA fallback needed in FastAPI.
  const selected = params.get("id");

  const [runs, setRuns] = useState<RunRow[]>([]);
  const [events, setEvents] = useState<StoredEvent[]>([]);

  useEffect(() => {
    fetchRuns(200).then(setRuns).catch(() => setRuns([]));
  }, []);

  useEffect(() => {
    if (!selected) return setEvents([]);
    fetchRunEvents(Number(selected)).then(setEvents).catch(() => setEvents([]));
  }, [selected]);

  const run = runs.find((r) => String(r.id) === selected);

  return (
    <div className="flex flex-col gap-4">
      {selected ? (
        <>
          <button onClick={() => router.push("/runs/")} className="self-start rounded border px-2 py-1 text-sm">
            ← all runs
          </button>
          <h1 className="text-lg font-semibold">Run #{selected}</h1>
          {run ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {[
                ["wave", run.wave ?? "-"], ["coins", run.coins ?? "-"], ["tier", run.tier ?? "-"],
                ["taps", run.tap_count], ["scans", run.scan_count],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-lg border p-3">
                  <div className="text-xs uppercase text-muted-foreground">{label}</div>
                  <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
                </div>
              ))}
            </div>
          ) : null}
          <EventFeed events={events.map((row) => ({ ...row, ...row.detail }) as unknown as BotEvent)} />
        </>
      ) : (
        <RunTable runs={runs} onSelect={(id) => router.push(`/runs/?id=${id}`)} />
      )}
    </div>
  );
}

export default function RunsPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <Runs />
    </Suspense>
  );
}
```

- [ ] **Step 5: Write the Stats page**

Create `web/ui/app/stats/page.tsx`, following whatever the `dataviz` skill specified for colours, axes and empty states:

```tsx
"use client";

import { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { fetchStats } from "@/lib/api";
import type { StatsPayload } from "@/lib/types";

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border p-3">
      <h2 className="mb-3 text-xs uppercase tracking-wide text-muted-foreground">{title}</h2>
      <div className="h-64">{children}</div>
    </section>
  );
}

export default function StatsPage() {
  const [stats, setStats] = useState<StatsPayload | null>(null);
  useEffect(() => { fetchStats().then(setStats).catch(() => setStats(null)); }, []);

  if (!stats) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (!stats.runs.length) {
    // An explicit empty state, never zeros that look like real data.
    return <p className="text-sm text-muted-foreground">No stored runs yet. (Running with --no-store?)</p>;
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Wave per run">
        <ResponsiveContainer>
          <LineChart data={stats.runs}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
            <XAxis dataKey="id" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Line type="monotone" dataKey="wave" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="Run length (s)">
        <ResponsiveContainer>
          <LineChart data={stats.runs}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
            <XAxis dataKey="id" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Line type="monotone" dataKey="duration" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="Taps by action">
        <ResponsiveContainer>
          <BarChart data={stats.taps}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
            <XAxis dataKey="action" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Bar dataKey="count" />
          </BarChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="Events by screen">
        <ResponsiveContainer>
          <BarChart data={stats.screens}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
            <XAxis dataKey="screen" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Bar dataKey="count" />
          </BarChart>
        </ResponsiveContainer>
      </Panel>
    </div>
  );
}
```

- [ ] **Step 6: Write the Errors page**

Create `web/ui/app/errors/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { SnapshotStrip } from "@/components/SnapshotStrip";
import { fetchErrors, fetchUnknown } from "@/lib/api";
import { clock } from "@/lib/format";
import type { Snapshot, StoredEvent } from "@/lib/types";

export default function ErrorsPage() {
  const [errors, setErrors] = useState<StoredEvent[]>([]);
  const [shots, setShots] = useState<Snapshot[]>([]);

  useEffect(() => {
    fetchErrors().then(setErrors).catch(() => setErrors([]));
    fetchUnknown().then(setShots).catch(() => setShots([]));
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-lg border p-3">
        <h2 className="mb-3 text-xs uppercase tracking-wide text-muted-foreground">Errors</h2>
        {errors.length ? (
          <ul className="flex flex-col gap-2">
            {errors.map((row) => (
              <li key={row.seq} className="rounded border p-2">
                <div className="font-mono text-xs text-muted-foreground">{clock(row.ts)}</div>
                <div className="text-sm text-red-500">{String(row.detail.message ?? "")}</div>
                {row.detail.traceback ? (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-xs text-muted-foreground">traceback</summary>
                    <pre className="mt-1 overflow-x-auto text-xs">{String(row.detail.traceback)}</pre>
                  </details>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No errors recorded.</p>
        )}
      </section>

      <section className="rounded-lg border p-3">
        <h2 className="mb-3 text-xs uppercase tracking-wide text-muted-foreground">Unknown screens</h2>
        <SnapshotStrip shots={shots} />
      </section>
    </div>
  );
}
```

- [ ] **Step 7: Build, test, and walk every page**

```bash
cd web/ui && npm test && npm run build && cd ../.. && uv run pytest -q
uv run tower_bot.py --web
```

Visit `/`, `/runs/`, `/runs/?id=1`, `/stats/`, `/errors/`. Reload `/runs/?id=1` directly to confirm the deep link survives a refresh — that is the whole reason for the query-string design. Then run once with `--no-store` and confirm every page shows its empty state rather than zeros.

- [ ] **Step 8: Commit**

```bash
git add web/ui web/static
git commit -m "feat: add the runs, stats and errors pages"
```

---

## Done when

- `uv run pytest -q` and `npm test` both pass.
- The Live page shows the emulator screen updating as the bot scans, with match boxes that can be toggled off and a green box on the upgrade that was tapped.
- Ten open dashboard tabs cost one JPEG encode per scan, not ten.
- Closing the last tab, and Ctrl+C with a tab still open, both exit cleanly — the MJPEG response does not wedge shutdown.
- `/runs/?id=N` deep-links to one run's stats and full event timeline, and survives a page refresh.
- `/stats/` charts wave, run length, taps by action and events by screen across every stored run.
- `/errors/` lists `BotError` rows with expandable tracebacks, plus the unknown-screen snapshots.
- Every page shows an explicit empty state under `--no-store`, never zeros.
- `db.reader()` still opens `mode=ro` and the test proving it still passes.
- `web/static/` is rebuilt and committed; the freshness test passes.
