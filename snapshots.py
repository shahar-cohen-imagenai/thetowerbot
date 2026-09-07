"""Save frames the bot could not classify.

These are the diagnostic trail for UNKNOWN, and the raw material for adding
new screens later. Rate-limited, deduplicated and capped so a long unattended
session cannot fill the disk - or, worse, fill it with fifty pictures of the
same unmodelled screen and evict every genuine find.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2
import numpy as np

from device import Image

logger = logging.getLogger("tower_bot.snapshots")

# The hash grid. Small on purpose: a frame averaged down to 72 cells keeps
# its layout and loses everything that moves on a live screen - a cash
# counter ticking, an animation frame, a particle. Layout is what makes a
# screen a different screen.
_HASH_WIDTH = 9
_HASH_HEIGHT = 8


def perceptual_hash(image: Image) -> int:
    """A 64-bit difference hash: is each cell brighter than its right neighbour?

    Comparisons between neighbours rather than absolute levels, so a screen
    that only dims or brightens - a fade, a dialog's scrim - keeps its hash.
    """
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(
        grey, (_HASH_WIDTH, _HASH_HEIGHT), interpolation=cv2.INTER_AREA
    )
    # int, not the raw ndarray: hashes are compared with XOR and stored as
    # dict values, and a numpy scalar would drag dtype semantics into both.
    bits = np.packbits(small[:, 1:] > small[:, :-1])
    return int.from_bytes(bits.tobytes(), "big")


def distance(left: int, right: int) -> int:
    """Hamming distance - how many of the 64 comparisons disagree."""
    return (left ^ right).bit_count()


class SnapshotWriter:
    def __init__(
        self,
        directory: Path,
        min_interval: float = 30.0,
        keep: int = 50,
        max_distance: int = 8,
    ) -> None:
        self._dir = Path(directory)
        self._min_interval = min_interval
        self._keep = keep
        self._max_distance = max_distance
        self._last = float("-inf")
        # Loaded from disk on first use rather than here, so constructing a
        # writer stays free and a directory that fills up between construction
        # and first write is still seen. None means "not loaded yet", which is
        # the state an empty directory cannot be confused with.
        self._hashes: dict[Path, int] | None = None

    def maybe_write(self, image: Image, now: float | None = None) -> Path | None:
        """Save the frame unless it is too soon, or this screen is already kept.

        Two gates, answering different questions. The rate limit asks how
        often; the fingerprint asks whether this is anything new. A bot parked
        on one unmodelled screen fails the second one every scan, which is the
        point - the interval alone would still let it spend all `keep` slots
        on that single screen and evict every other find.

        Two clocks on purpose. The rate limit uses `now` / `time.monotonic()`,
        which cannot jump backwards. The FILENAME uses the wall clock, because
        monotonic's epoch is boot-relative: it names no real moment and resets
        every time the host reboots. Nanoseconds, so back-to-back writes
        cannot collide.
        """
        moment = time.monotonic() if now is None else now
        if moment - self._last < self._min_interval:
            return None

        fingerprint = perceptual_hash(image)
        if self._duplicate_of(fingerprint) is not None:
            # Deliberately without touching `self._last`. The rate limit caps
            # WRITES, not calls: a screen genuinely new must not be made to
            # wait another interval because a known one was offered first.
            return None

        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{time.time_ns()}.png"
        cv2.imwrite(str(path), image)
        self._known()[path] = fingerprint
        self._last = moment
        self._prune()
        return path

    def _duplicate_of(self, fingerprint: int) -> Path | None:
        """The kept snapshot this frame is a repeat of, if there is one."""
        for path, known in self._known().items():
            if distance(fingerprint, known) <= self._max_distance:
                return path
        return None

    def _known(self) -> dict[Path, int]:
        """Fingerprints of the snapshots currently kept, read in on first use.

        Read from disk rather than tracked only for this session: the
        directory outlives the process, so a screen captured yesterday would
        otherwise be captured again on every launch.
        """
        if self._hashes is None:
            self._hashes = {}
            for path in sorted(self._dir.glob("*.png")):
                existing = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if existing is None:
                    logger.warning("unreadable snapshot ignored: %s", path)
                    continue
                self._hashes[path] = perceptual_hash(existing)
        return self._hashes

    def _prune(self) -> None:
        existing = sorted(self._dir.glob("*.png"), key=lambda p: p.stat().st_mtime)
        for stale in existing[: max(0, len(existing) - self._keep)]:
            stale.unlink(missing_ok=True)
            # A screen stops being a duplicate the moment its only copy is
            # gone, or the directory could drain to empty while the writer
            # went on refusing to refill it.
            self._known().pop(stale, None)
