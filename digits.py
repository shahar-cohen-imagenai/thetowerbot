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
import re
from pathlib import Path

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


# A filesystem cannot hold a file called ".png", and "," is awkward in a
# shell, so punctuation glyphs are stored under a name and mapped back here.
GLYPH_FILENAMES: dict[str, str] = {
    **{ch: ch for ch in "0123456789KMBT"},
    ".": "dot",
    ",": "comma",
    "$": "dollar",
    # Death-modal captions. The numbers there are not bare: the lines read
    # "Wave 1" / "Tier 1" / "0 (c)", and they are CENTRED, so the digits slide
    # left as they grow and a crop tight to the number would miss it. The
    # caption has to be inside the region, so the atlas has to know it.
    # Lowercase names are spelled out: macOS filesystems are case-insensitive,
    # so "t.png" and "T.png" would be the same file.
    "W": "cap_w",
    "a": "cap_a",
    "v": "cap_v",
    "e": "cap_e",
    "i": "cap_i",
    "r": "cap_r",
    "\u00a9": "coin",
}

# Glyphs that may legally surround the number without invalidating the read.
# "T" is deliberately in here AND in SUFFIXES: Tier's initial and the trillions
# suffix are the same glyph. Which one it is comes from POSITION, not from the
# character - see parse_number.
CAPTION_GLYPHS: frozenset[str] = frozenset("WaveTier") | {"\u00a9"}
GLYPH_LABELS: dict[str, str] = {name: char for char, name in GLYPH_FILENAMES.items()}

# The three text sizes the UI renders numbers at. matchTemplate is not
# scale-invariant, so each needs its own atlas.
SIZE_CLASSES: tuple[str, ...] = ("wallet", "price", "modal")


class Atlas:
    """Labelled glyph images for one size class."""

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._glyphs: dict[str, Image] = {}
        for path in sorted(self._dir.glob("*.png")):
            label = GLYPH_LABELS.get(path.stem)
            if label is None:
                logger.warning("ignoring unlabelled atlas file: %s", path.name)
                continue
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                logger.warning("unreadable atlas file: %s", path.name)
                continue
            self._glyphs[label] = image

    @property
    def labels(self) -> set[str]:
        return set(self._glyphs)

    def match(
        self, glyph: Image, threshold: float = config.GLYPH_MATCH_THRESHOLD
    ) -> str | None:
        """Best-scoring label for `glyph`, or None if nothing clears threshold.

        The glyph is resized to each candidate rather than the other way
        round: segmentation crops tightly, so shapes are comparable even when
        the capture is a pixel or two off the atlas entry's size.
        """
        best_label: str | None = None
        best_score = -1.0

        for label, template in self._glyphs.items():
            resized = cv2.resize(
                glyph, (template.shape[1], template.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
            score = float(
                cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED)[0][0]
            )
            if score > best_score:
                best_label, best_score = label, score

        if best_score < threshold:
            return None
        return best_label


class AtlasCache:
    """One Atlas per size class, loaded on first use."""

    def __init__(self, root: Path = config.ATLAS_DIR) -> None:
        self._root = Path(root)
        self._cache: dict[str, Atlas | None] = {}

    def get(self, size_class: str) -> Atlas | None:
        """The atlas for `size_class`, or None if it has not been built.

        None rather than an exception: an unbuilt atlas must degrade the bot
        to brightness affordability, not stop it from running at all.
        """
        if size_class in self._cache:
            return self._cache[size_class]

        directory = self._root / size_class
        atlas: Atlas | None = None
        if directory.is_dir():
            candidate = Atlas(directory)
            if candidate.labels:
                atlas = candidate
            else:
                logger.warning("atlas directory %s has no labelled glyphs", directory)
        else:
            logger.warning("no atlas for size class %r at %s", size_class, directory)

        self._cache[size_class] = atlas
        return atlas


# The game abbreviates large numbers. A long-running bot hits these within
# an hour or two, and a digit-only parser misreads them silently.
SUFFIXES: dict[str, int] = {
    "K": 1_000,
    "M": 1_000_000,
    "B": 1_000_000_000,
    "T": 1_000_000_000_000,
}

# A number is a digit run, optionally decimal, optionally suffixed. Anchoring
# on the digits is what disambiguates "Tier1" (caption T, the number is 1) from
# "1.5T" (the number is 1.5 trillion): the suffix letter only counts when it
# trails digits, which is exactly what this pattern encodes.
_NUMBER_RUN = re.compile(r"\d+(?:\.\d+)?[KMBT]?")


def parse_number(text: str) -> int | None:
    """`$1.23K` -> 1230, `Wave 137` -> 137. None if it is not one number.

    The string is whatever the atlas recognised, so it may carry a caption:
    the death modal renders "Wave 1", not "1". Captions are allowed around the
    number, but three things still make a read fail, because each of them
    means we are not sure what we are looking at:

    * no number at all
    * more than one number - which is the real one?
    * any leftover glyph that is not a known caption
    """
    cleaned = text.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None

    runs = _NUMBER_RUN.findall(cleaned)
    if len(runs) != 1:
        return None

    leftover = _NUMBER_RUN.sub("", cleaned)
    if any(char not in CAPTION_GLYPHS for char in leftover):
        return None

    token = runs[0]
    multiplier = 1
    if token[-1] in SUFFIXES:
        multiplier = SUFFIXES[token[-1]]
        token = token[:-1]

    return int(round(float(token) * multiplier))


class NumberReader:
    """Turns a screen region into an integer, or None."""

    def __init__(self, atlases: AtlasCache | None = None) -> None:
        self._atlases = atlases if atlases is not None else AtlasCache()

    def read(
        self,
        screen: Image,
        region: config.Region,
        anchor: tuple[int, int],
        size_class: str,
    ) -> int | None:
        """Read the number in `region`, measured from `anchor`.

        Any failure along the way - no atlas, region off-screen, a glyph
        that matches nothing - returns None. A partially recognised number
        is NOT returned: reading "1?34" as 134 would let the bot act on a
        price that looks plausible and is wrong by an order of magnitude.
        """
        atlas = self._atlases.get(size_class)
        if atlas is None:
            return None

        patch = crop(screen, region, anchor)
        if patch is None:
            return None

        glyphs = split_glyphs(binarize(patch))
        if not glyphs:
            return None

        labels: list[str] = []
        for glyph in glyphs:
            label = atlas.match(glyph)
            if label is None:
                logger.debug("unrecognised glyph in %s region", size_class)
                return None
            labels.append(label)

        return parse_number("".join(labels))
