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
    shutdown = threading.Event()
    app = create_app(
        state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None,
        unknown_dir=config.UNKNOWN_DIR, shutdown=shutdown, frames=frames,
    )
    return TestClient(app), frames, shutdown


def test_still_is_404_before_the_first_scan(wired) -> None:
    client, _, _ = wired
    response = client.get("/api/frame.jpg")
    assert response.status_code == 404
    # Distinct from the no-buffer-at-all case below: a frame is on its way,
    # just not captured yet.
    assert response.json()["detail"] == "no frame captured yet"


def test_still_is_404_with_a_distinct_reason_when_there_is_no_frame_buffer() -> None:
    """--once, --tui, or plain logging never construct a FrameBuffer at all.
    That is a different fact than a buffer that simply has nothing in it yet
    (see test_still_is_404_before_the_first_scan), and frame_still() must
    not conflate the two the way it once did."""
    app = create_app(
        state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None,
        unknown_dir=config.UNKNOWN_DIR,
    )
    client = TestClient(app)
    response = client.get("/api/frame.jpg")
    assert response.status_code == 404
    assert response.json()["detail"] == "no frame buffer"


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


def test_the_stream_ends_when_shutdown_is_set(wired) -> None:
    """The hang the SSE stream already hit once, not re-introduced.

    An in-flight response uvicorn's graceful shutdown waits on forever is
    exactly what `shutdown` exists to prevent - and a held-open <img> never
    disconnects on its own just because the bot ended.
    """
    _, frames, shutdown = wired
    frames.publish(a_frame())

    async def drain() -> int:
        chunks = 0
        shutdown.set()
        async for _ in frame_stream(
            frames, lambda: _never(), shutdown=shutdown, poll=0.01
        ):
            chunks += 1
        return chunks

    async def _never() -> bool:
        return False

    assert asyncio.run(drain()) == 0
