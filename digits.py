"""Read a number off the screen.

    region  ->  binarise  ->  split into glyphs  ->  match atlas  ->  parse

Every region is anchored to a matched template, never to absolute pixels:
the death modal shifts ~46px vertically depending on whether the
"New Highest Wave!" line is present.

Every public entry point returns None rather than raising. A misread number
must degrade the bot to its phase-2 behaviour, never stop the scan loop.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

import config
from device import Image

logger = logging.getLogger("tower_bot.digits")


def crop(screen: Image, region: config.Region, anchor: tuple[int, int]) -> Image | None:
    """Cut `region` out of `screen`, measured from `anchor`'s top-left.

    Returns None if the rectangle falls outside the frame - a wrong anchor
    or a resolution change should read as "no number here", not an
    IndexError inside the scan loop.
    """
    x = anchor[0] + region.dx
    y = anchor[1] + region.dy
    height, width = screen.shape[:2]

    if x < 0 or y < 0 or x + region.w > width or y + region.h > height:
        logger.debug(
            "region %s from anchor %s falls outside %dx%d", region, anchor, width, height
        )
        return None

    patch = screen[y : y + region.h, x : x + region.w]
    if patch.size == 0:
        return None
    return patch


def binarize(region: Image, threshold: int = config.DIGIT_BINARY_THRESHOLD) -> Image:
    """Light glyphs -> 255, dark panel -> 0."""
    grey = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(grey, threshold, 255, cv2.THRESH_BINARY)
    return binary


def glyph_spans(
    binary: Image, min_width: int = config.GLYPH_MIN_WIDTH
) -> list[tuple[int, int]]:
    """Column ranges [x0, x1) holding a glyph, left to right.

    Column projection rather than contours: the game's font has no
    disconnected glyphs at these sizes, and a projection is trivial to
    reason about when a threshold is slightly off.
    """
    columns = (binary > 0).any(axis=0)
    spans: list[tuple[int, int]] = []
    start: int | None = None

    for x, filled in enumerate(columns):
        if filled and start is None:
            start = x
        elif not filled and start is not None:
            if x - start >= min_width:
                spans.append((start, x))
            start = None

    if start is not None and len(columns) - start >= min_width:
        spans.append((start, len(columns)))
    return spans


def split_glyphs(binary: Image, min_width: int = config.GLYPH_MIN_WIDTH) -> list[Image]:
    """One tightly-cropped binary image per glyph, left to right."""
    glyphs: list[Image] = []
    for x0, x1 in glyph_spans(binary, min_width):
        column = binary[:, x0:x1]
        rows = np.flatnonzero((column > 0).any(axis=1))
        if rows.size == 0:
            continue
        glyphs.append(column[rows[0] : rows[-1] + 1, :])
    return glyphs
