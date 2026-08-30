from __future__ import annotations

from pathlib import Path

import numpy as np

from snapshots import SnapshotWriter


def blank() -> np.ndarray:
    return np.zeros((40, 30, 3), dtype=np.uint8)


def test_writes_a_snapshot(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path)
    path = writer.maybe_write(blank(), now=1000.0)

    assert path is not None
    assert Path(path).exists()


def test_rate_limits_within_the_interval(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path, min_interval=30.0)

    assert writer.maybe_write(blank(), now=1000.0) is not None
    assert writer.maybe_write(blank(), now=1010.0) is None
    assert writer.maybe_write(blank(), now=1031.0) is not None


def test_prunes_to_the_keep_limit(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path, min_interval=0.0, keep=3)

    for i in range(6):
        writer.maybe_write(blank(), now=1000.0 + i)

    assert len(list(tmp_path.glob("*.png"))) == 3


def test_filenames_use_the_wall_clock_not_the_monotonic_clock(tmp_path: Path) -> None:
    """The spec asks for `unknown/<ts>.png`. `time.monotonic()` has an
    arbitrary boot-relative epoch, so it names no real moment and resets on
    every host reboot. The rate limit keeps using monotonic - two clocks, on
    purpose - but the filename must be a wall-clock timestamp.
    """
    import time

    writer = SnapshotWriter(tmp_path)

    before = time.time_ns()
    path = writer.maybe_write(blank(), now=1000.0)  # monotonic-style "now"
    after = time.time_ns()

    assert path is not None
    assert before <= int(path.stem) <= after


def test_the_rate_limit_still_uses_the_injected_monotonic_clock(
    tmp_path: Path,
) -> None:
    """Guard against the two clocks being conflated the other way."""
    writer = SnapshotWriter(tmp_path, min_interval=30.0)

    assert writer.maybe_write(blank(), now=1000.0) is not None
    assert writer.maybe_write(blank(), now=1029.0) is None
