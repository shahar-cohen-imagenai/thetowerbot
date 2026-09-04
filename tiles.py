"""Find the upgrade tiles on a page, and what is written in them.

A tile is a bright bordered rectangle. Everything the bot may tap lives
inside one, so detecting tiles is what lets an upgrade be addressed by the
name written on it rather than by a template cut from it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import cv2

import config
import ocr
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


@dataclass(frozen=True)
class Row:
    """One upgrade the bot can see, and everything needed to buy it.

    `name` is the RAW text as read, not the normalised form: it is what an
    event reports when nothing matched, and "read 'Criticai Chance'" is only
    useful if it says what was actually on the screen. Matching normalises
    both sides at comparison time instead.
    """

    name: str
    price: int | None
    tap: tuple[int, int]
    rect: Rect
    confidence: float


def normalise(text: str) -> str:
    """Case-fold and strip everything that is not a letter or digit.

    All-caps text loses its spaces in OCR ('ATTACKUPGRADES'), so comparing
    raw strings would refuse a row that was read perfectly well. Stripping
    punctuation also makes 'Coins/Kill Bonus' survive a missing slash.
    """
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _centre(rect: Rect) -> tuple[int, int]:
    return rect.x + rect.w // 2, rect.y + rect.h // 2


def rows_from(boxes: tuple[ocr.TextBox, ...], found: tuple[Rect, ...]) -> tuple[Row, ...]:
    """Assign text boxes to tiles, then read each tile as one row.

    Pure, and separate from read_rows(), so the tests can drive it from
    recorded JSON without loading the OCR engine.
    """
    rows: list[Row] = []
    for tile in found:
        inside = [box for box in boxes if _contains(tile, *_centre(box.rect))]
        if not inside:
            continue

        # A full-width unlock tile centres its label, so position alone
        # cannot split label from price. The split is by "does this text
        # hold a digit" first, then by config.TILE_PRICE_TOP_FRACTION
        # second, which is what keeps a stat value ('3') from being read as
        # the price ('30').
        #
        # "Holds a digit", not ocr.parse_number(...) is None: the Critical
        # Chance/Factor tiles' own stat readout - '1.00%', 'x1.20' - is
        # exactly the kind of text ocr.parse_number is documented to REFUSE
        # (it is neither a price nor a plain balance), so testing
        # parse_number(...) is None would count it as part of the name and
        # produce '1.00% Critical Chance' instead of 'Critical Chance'.
        # Measured across every box in all four recorded fixtures
        # (tests/fixtures/ocr/*.json): every real label fragment ('Damage',
        # 'Critical', 'Chance', 'Unlock Cash Bonuses', ...) is digit-free,
        # and every value/readout/price box ('3', '1.00%', 'x1.20', '$10',
        # '0.00/sec', ...) holds at least one digit - a clean split with no
        # exceptions, and one rule for every tile rather than a per-row fix.
        label_boxes = [
            box for box in inside
            if not any(char.isdigit() for char in box.text)
        ]
        price_boxes = [
            box for box in inside
            if ocr.parse_number(box.text) is not None
            and box.rect.y >= tile.y + tile.h * config.TILE_PRICE_TOP_FRACTION
        ]

        if not label_boxes:
            continue
        # Top-to-bottom, then left-to-right: a wrapped label reads down.
        label_boxes.sort(key=lambda b: (b.rect.y, b.rect.x))
        name = " ".join(box.text for box in label_boxes)

        # Lowest price box wins: the value panel sits above the price panel,
        # and a stat value that happens to parse as a number ('3') would
        # otherwise be read as the price.
        price = None
        if price_boxes:
            price_boxes.sort(key=lambda b: b.rect.y)
            price = ocr.parse_number(price_boxes[-1].text)

        rows.append(
            Row(
                name=name,
                price=price,
                tap=_centre(tile),
                rect=tile,
                confidence=min(box.confidence for box in label_boxes),
            )
        )
    return tuple(rows)


def read_rows(screen: Image) -> tuple[Row, ...]:
    """Every upgrade visible on `screen`."""
    return rows_from(ocr.read(screen), find_tiles(screen))
