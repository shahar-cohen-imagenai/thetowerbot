"""The menu header, against committed captures.

The header is white text on a LIGHT panel, unlike every other number the bot
reads, which is light text on a dark one. At the shared default threshold the
panel survives binarisation and glues adjacent glyphs together: "1.77K"
segments as 1 . 77 K, and a merged span matches nothing, so the read fails.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import config
import digits

FIXTURES = Path(__file__).parent / "fixtures"
COINS = (85, 152, 170, 68)


def header_patch(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    x, y, w, h = COINS
    return img[y : y + h, x : x + w]


def test_default_threshold_merges_adjacent_glyphs() -> None:
    """Why this task exists. If this ever starts passing at 140, the font
    changed and the header threshold should be re-measured, not deleted."""
    patch = header_patch("menu_workshop_attack")
    spans = digits.glyph_spans(digits.binarize(patch, 140))
    assert len(spans) == 4, "expected the two 7s of 1.77K to merge at 140"


def test_header_threshold_separates_every_glyph() -> None:
    patch = header_patch("menu_workshop_attack")
    threshold = digits.threshold_for("header")
    spans = digits.glyph_spans(digits.binarize(patch, threshold))
    assert len(spans) == 5, f"1.77K must split into 1 . 7 7 K, got {len(spans)}"


def test_unknown_size_class_gets_the_shared_default() -> None:
    assert digits.threshold_for("price") == config.DIGIT_BINARY_THRESHOLD
    assert digits.threshold_for("nonsense") == config.DIGIT_BINARY_THRESHOLD


def test_header_threshold_sits_inside_the_measured_plateau() -> None:
    """170-240 all segment correctly; the default is the middle of that, not
    an edge, so a slightly different capture does not fall off it."""
    assert 170 <= digits.threshold_for("header") <= 240
