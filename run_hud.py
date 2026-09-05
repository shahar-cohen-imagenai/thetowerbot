"""Finding the run HUD's cash counter.

The counter is the one part of the in-run UI that is in the same place on
every upgrade tab and says, by existing at all, that a run is in progress -
cash is not shown anywhere outside one. That makes it a better screen probe
than the upgrade panel's header bar, which is a different crop per tab, and
a better base for the wallet region than the panel anchor, which is pinned
to the opposite end of the screen from the wallet.

Kept apart from screens.py because both the classifier and the wallet read
need it, and neither should be importing the other.
"""

from __future__ import annotations

from typing import NamedTuple

import config
import vision
from device import Image


class CashMatch(NamedTuple):
    score: float
    top_left: tuple[int, int] | None


def find_cash(
    screen: Image,
    cache: vision.TemplateCache,
    threshold: float = config.ANCHOR_THRESHOLD,
) -> CashMatch:
    """Locate the cash counter. `top_left` is None below `threshold`.

    Searched inside config.RUN_CASH_SEARCH rather than full-frame: an
    upgrade's price carries the same "$" glyph, and a full-frame match would
    happily return one of those on a workshop page.
    """
    box = config.RUN_CASH_SEARCH
    h, w = screen.shape[:2]
    if box.dy + box.h > h or box.dx + box.w > w:
        return CashMatch(0.0, None)

    window = screen[box.dy:box.dy + box.h, box.dx:box.dx + box.w]
    score, top_left = vision.best_score(window, cache.get(config.RUN_CASH_ANCHOR))
    if score < threshold:
        return CashMatch(score, None)
    return CashMatch(score, (top_left[0] + box.dx, top_left[1] + box.dy))
