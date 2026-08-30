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
