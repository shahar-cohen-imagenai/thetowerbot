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

    def mark_tapped(self, name: str) -> None:
        """Flag a recorded box as the one that was actually tapped."""
        with self._lock:
            for box in self._boxes:
                if box["name"] == name:
                    box["tapped"] = True

    def boxes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(box) for box in self._boxes]

    def latest(self) -> tuple[int, bytes] | None:
        """The current frame number and its JPEG bytes.

        Returns None if the buffer has never been fed a frame, or if the most
        recent encode attempt failed - the latter is self-healing, since a
        failed encode never poisons the cache and the next call retries.

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
