"""The atlas bootstrap tool. Labelling is manual; segmentation is not."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

import build_atlas
import config
from tests.test_digits import render_text  # needs tests/__init__.py


def scene(text: str) -> tuple[np.ndarray, config.Region]:
    rendered = render_text(text)
    screen = np.zeros((200, 400, 3), dtype=np.uint8)
    screen[0 : rendered.shape[0], 0 : rendered.shape[1]] = rendered
    return screen, config.Region(dx=0, dy=0, w=rendered.shape[1], h=rendered.shape[0])


def test_dumps_one_file_per_glyph(tmp_path: Path) -> None:
    screen, region = scene("407")
    written = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)

    assert len(written) == 3
    assert all(p.exists() for p in written)
    assert sorted(p.name for p in written) == [
        "glyph_000.png", "glyph_001.png", "glyph_002.png"
    ]


def test_dumped_glyphs_are_readable_greyscale(tmp_path: Path) -> None:
    screen, region = scene("5")
    written = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)
    img = cv2.imread(str(written[0]), cv2.IMREAD_GRAYSCALE)
    assert img is not None and img.size > 0


def test_does_not_overwrite_existing_numbering(tmp_path: Path) -> None:
    """Called twice over a session, the second batch must not clobber the first."""
    screen, region = scene("12")
    build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)
    second = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)

    assert len(list(tmp_path.glob("glyph_*.png"))) == 4
    assert second[0].name == "glyph_002.png"
