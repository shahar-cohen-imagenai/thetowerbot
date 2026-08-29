"""Save frames the bot could not classify.

These are the diagnostic trail for UNKNOWN, and the raw material for adding
new screens later. Rate-limited and capped so a long unattended session
cannot fill the disk.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2

from device import Image

logger = logging.getLogger("tower_bot.snapshots")


class SnapshotWriter:
    def __init__(
        self, directory: Path, min_interval: float = 30.0, keep: int = 50
    ) -> None:
        self._dir = Path(directory)
        self._min_interval = min_interval
        self._keep = keep
        self._last = float("-inf")

    def maybe_write(self, image: Image, now: float | None = None) -> Path | None:
        moment = time.monotonic() if now is None else now
        if moment - self._last < self._min_interval:
            return None

        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{int(moment * 1000)}.png"
        cv2.imwrite(str(path), image)
        self._last = moment
        self._prune()
        return path

    def _prune(self) -> None:
        existing = sorted(self._dir.glob("*.png"), key=lambda p: p.stat().st_mtime)
        for stale in existing[: max(0, len(existing) - self._keep)]:
            stale.unlink(missing_ok=True)
