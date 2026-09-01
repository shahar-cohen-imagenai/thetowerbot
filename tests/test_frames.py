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
