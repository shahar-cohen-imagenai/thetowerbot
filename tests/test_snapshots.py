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
