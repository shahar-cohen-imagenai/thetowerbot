"""Find the upgrade tiles on a page, and what is written in them.

A tile is a bright bordered rectangle. Everything the bot may tap lives
inside one, so detecting tiles is what lets an upgrade be addressed by the
name written on it rather than by a template cut from it.
"""

from __future__ import annotations

import logging

import cv2

import config
from config import Rect  # re-exported: tiles.Rect is the name callers use
from device import Image

logger = logging.getLogger("tower_bot.tiles")


def _contains(rect: Rect, x: int, y: int) -> bool:
    return rect.x <= x < rect.x + rect.w and rect.y <= y < rect.y + rect.h


def candidates(screen: Image) -> tuple[Rect, ...]:
    """Every outermost bordered rectangle, before the size filter.

    Exposed for tools/tile_preview.py: seeing what the filter rejected is
    how the bounds in config get chosen.
    """
    grey = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(grey, config.TILE_BINARY_THRESHOLD, 255, cv2.THRESH_BINARY)
    # RETR_EXTERNAL, not RETR_LIST: a tile's value and price panels are
    # bordered rectangles INSIDE the tile, and external-only is what drops
    # them. The size filter below is the second line of defence, not the
    # first.
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return tuple(Rect(*cv2.boundingRect(c)) for c in contours)


def find_tiles(screen: Image) -> tuple[Rect, ...]:
    """The upgrade tiles, top to bottom then left to right.

    No region-cropping step: the height bound alone separates real tiles
    (measured 196px tall) from the HUD panels above the upgrade area in
    in_run_lit.png (measured 158-160px tall) and from the bottom nav bar in
    the menu fixtures (measured ~100-106px tall) - see the TILE_MIN_H
    comment in config.py for the measurement this bound is built from.
    """
    kept = [
        rect
        for rect in candidates(screen)
        if config.TILE_MIN_W <= rect.w <= config.TILE_MAX_W
        and config.TILE_MIN_H <= rect.h <= config.TILE_MAX_H
    ]
    # Reading order, so a caller can rely on priority-by-position without
    # sorting again.
    return tuple(sorted(kept, key=lambda r: (r.y, r.x)))
