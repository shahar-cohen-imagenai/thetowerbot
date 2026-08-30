"""Digit reading. Fixtures are golden data - failures mean a capture drifted."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

import config
import digits

FIXTURES = Path(__file__).parent / "fixtures"


def test_crop_is_relative_to_the_anchor() -> None:
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    screen[30:40, 50:70] = 255  # a white bar at (50,30), 20x10

    region = config.Region(dx=10, dy=5, w=20, h=10)
    crop = digits.crop(screen, region, anchor=(40, 25))

    assert crop is not None
    assert crop.shape[:2] == (10, 20)
    assert crop.mean() == 255.0  # we landed exactly on the bar


def test_crop_off_screen_returns_none() -> None:
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    region = config.Region(dx=0, dy=0, w=20, h=10)
    assert digits.crop(screen, region, anchor=(95, 95)) is None


def test_crop_with_negative_offsets() -> None:
    """The anchor is not always above-left of the number it locates."""
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    screen[10:20, 10:30] = 255
    region = config.Region(dx=-30, dy=-30, w=20, h=10)
    crop = digits.crop(screen, region, anchor=(40, 40))
    assert crop is not None
    assert crop.mean() == 255.0


def render_text(text: str, scale: float = 2.0, thickness: int = 3) -> np.ndarray:
    """White text on black, the same polarity as the game's HUD.

    Synthetic on purpose: it exercises the segmentation algorithm without an
    emulator, which the suite must run without.
    """
    canvas = np.zeros((80, 60 * len(text) + 40, 3), dtype=np.uint8)
    cv2.putText(
        canvas, text, (20, 55), cv2.FONT_HERSHEY_SIMPLEX, scale,
        (255, 255, 255), thickness, cv2.LINE_AA,
    )
    return canvas


def test_binarize_splits_light_glyphs_from_dark_background() -> None:
    binary = digits.binarize(render_text("8"))
    assert set(np.unique(binary)) <= {0, 255}
    assert (binary > 0).any()


def test_segments_each_digit_separately() -> None:
    binary = digits.binarize(render_text("1234"))
    spans = digits.glyph_spans(binary)
    assert len(spans) == 4


def test_segments_keep_left_to_right_order() -> None:
    binary = digits.binarize(render_text("1234"))
    spans = digits.glyph_spans(binary)
    assert spans == sorted(spans)
    assert all(x0 < x1 for x0, x1 in spans)


def test_decimal_point_survives_the_min_width_filter() -> None:
    """The dot is the narrowest real glyph. Filter it out and 1.5K reads 15K."""
    binary = digits.binarize(render_text("1.5K"))
    assert len(digits.glyph_spans(binary)) == 4


def test_split_glyphs_trims_vertically() -> None:
    glyphs = digits.split_glyphs(digits.binarize(render_text("7")))
    assert len(glyphs) == 1
    glyph = glyphs[0]
    assert (glyph[0, :] > 0).any(), "top row should touch the glyph"
    assert (glyph[-1, :] > 0).any(), "bottom row should touch the glyph"


def test_empty_region_yields_no_glyphs() -> None:
    blank = np.zeros((40, 100, 3), dtype=np.uint8)
    assert digits.split_glyphs(digits.binarize(blank)) == []
