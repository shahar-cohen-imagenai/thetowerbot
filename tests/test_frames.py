"""The frame buffer: one encode per frame, however many tabs are watching."""

from __future__ import annotations

import threading
import time
from unittest import mock

import cv2
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


def test_mark_tapped_flags_the_matching_box() -> None:
    buffer = FrameBuffer()
    buffer.publish(a_frame())
    buffer.add_box({"name": "Damage", "x": 1, "y": 2, "w": 3, "h": 4, "score": 0.9, "tapped": False})
    buffer.add_box({"name": "Health", "x": 5, "y": 6, "w": 7, "h": 8, "score": 0.8, "tapped": False})

    buffer.mark_tapped("Health")

    tapped = {box["name"]: box["tapped"] for box in buffer.boxes()}
    assert tapped == {"Damage": False, "Health": True}


def test_mark_tapped_with_no_matching_box_is_harmless() -> None:
    """Nothing was recorded under this name - not an error, just a no-op."""
    buffer = FrameBuffer()
    buffer.publish(a_frame())
    buffer.add_box({"name": "Damage", "x": 1, "y": 2, "w": 3, "h": 4, "score": 0.9, "tapped": False})

    buffer.mark_tapped("NoSuchAction")

    assert buffer.boxes() == [
        {"name": "Damage", "x": 1, "y": 2, "w": 3, "h": 4, "score": 0.9, "tapped": False}
    ]


def test_mark_tapped_takes_the_lock() -> None:
    """mark_tapped must block while another holder has the lock, and proceed
    once it is released - proof it goes through `with self._lock`, not a
    lock-free scan of `_boxes`."""
    buffer = FrameBuffer()
    buffer.publish(a_frame())
    buffer.add_box({"name": "Damage", "x": 1, "y": 2, "w": 3, "h": 4, "score": 0.9, "tapped": False})

    buffer._lock.acquire()
    completed = threading.Event()

    def marker() -> None:
        buffer.mark_tapped("Damage")
        completed.set()

    thread = threading.Thread(target=marker)
    thread.start()
    try:
        assert not completed.wait(timeout=0.2), "mark_tapped ran without waiting for the lock"
    finally:
        buffer._lock.release()
    assert completed.wait(timeout=2), "mark_tapped never completed after the lock was released"
    thread.join()
    assert buffer.boxes()[0]["tapped"] is True


def test_concurrent_publish_and_read_do_not_crash() -> None:
    """Smoke check only: hammers publish()/latest() from two threads and
    requires no exception and no malformed JPEG header.

    This does NOT prove the encode-outside-the-lock race is handled
    correctly: every field it touches (_frame, _number, _boxes, _encoded) is
    only ever whole-object reference-swapped, and the GIL already makes a
    single attribute read/write atomic, so there is no torn state for this
    test to observe even with no lock at all. See
    test_a_slow_stale_encode_does_not_evict_a_fresher_cache_entry for the
    test that actually forces the interleaving the re-check guards and checks
    for it: that a late-finishing encode of a superseded frame does not evict
    an already-cached fresher one and force a needless re-encode.
    """
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


def test_a_slow_stale_encode_does_not_evict_a_fresher_cache_entry() -> None:
    """Force the exact interleaving the re-check in latest() exists for: a
    late-finishing encode of an OLD frame returning AFTER a NEWER frame has
    already been encoded and cached.

    Note what this is not: the (number, payload) pair returned by any single
    latest() call is self-consistent by construction in every code path -
    frame and number are captured together, under the lock, before encoding
    ever starts - so removing the re-check cannot make one call return
    mismatched number/bytes (confirmed by hand-trace, and by running that
    assertion against the re-check removed: it still passes). What the
    re-check actually guards is the CACHE for *later* callers: without it, a
    slow reader's stale write clobbers an already-valid cache entry for the
    current frame, forcing a needless re-encode and breaking the "one encode
    per frame, however many readers" guarantee for everyone after it.

    cv2.imencode is monkeypatched so only its first invocation - the one
    that will encode the about-to-be-superseded frame - blocks before
    returning, until told to proceed. That guarantees the interleaving lands:
    the slow reader is inside its encode when the newer frame is published,
    encoded, and cached, and only then is it allowed to finish and race to
    write.
    """
    buffer = FrameBuffer()
    buffer.publish(a_frame(1))

    real_imencode = cv2.imencode
    call_count = 0
    entered_first_call = threading.Event()
    first_call_may_return = threading.Event()

    def gated_imencode(ext: str, img: np.ndarray, params: list[int]):
        nonlocal call_count
        call_count += 1
        is_first_call = call_count == 1
        if is_first_call:
            entered_first_call.set()
        result = real_imencode(ext, img, params)
        if is_first_call:
            first_call_may_return.wait(timeout=2)
        return result

    with mock.patch.object(cv2, "imencode", gated_imencode):
        stale_result: list[tuple[int, bytes] | None] = [None]

        def stale_reader() -> None:
            stale_result[0] = buffer.latest()

        reader_thread = threading.Thread(target=stale_reader)
        reader_thread.start()
        assert entered_first_call.wait(timeout=2), "stale reader never started encoding"

        # A newer frame lands and gets encoded and cached first, while the
        # stale reader above is still blocked mid-encode of the old one.
        buffer.publish(a_frame(2))
        fresh_first = buffer.latest()
        fresh_second = buffer.latest()
        assert fresh_first is not None and fresh_second is not None
        assert fresh_first[1] is fresh_second[1]  # ordinary cache hit

        # Now let the stale encoder finish and race to write its result.
        first_call_may_return.set()
        reader_thread.join()

        assert stale_result[0] is not None
        assert stale_result[0][0] == 1  # its own return is still self-consistent

        # The property under test: a later read of the CURRENT frame must
        # still be the object already cached above - the stale finisher must
        # not have evicted it and forced a needless re-encode.
        fresh_third = buffer.latest()
        assert fresh_third is not None
        assert fresh_third[1] is fresh_second[1], (
            "a late-finishing encode of the superseded frame evicted the "
            "current frame's cached bytes, forcing a needless re-encode"
        )
        assert call_count == 2, (
            f"expected exactly one imencode call per frame (2 total), got {call_count}"
        )
