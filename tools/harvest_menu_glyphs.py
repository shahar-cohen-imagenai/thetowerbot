"""Harvest menu glyphs from the COMMITTED menu fixtures, not a live device.

Menu prices render at three physical sizes: 22-23px in an upgrade row, 28-29px
in an unlock tile, 31-32px on a cards buy button (see task-5b-brief.md for the
measurements). `digits.Atlas` stores one image per label, so a size class can
only keep ONE size of each glyph - and `Atlas.match` resizes the CANDIDATE to
the TEMPLATE's size, so whichever size gets stored decides which direction
every future read has to rescale in. Downscaling a big candidate into a small
template is the one direction measured below the recognition threshold (a
28px "5" resized into a 22px template scored 0.67, under the 0.70 bar), while
every other pairing scored comfortably above it. So this harvester always
keeps the LARGEST instance of each glyph shape and discards smaller
duplicates - see `_absorb` below, which decides that from the pixels
themselves rather than from the order fixtures happen to be listed in.

Reads the committed fixtures rather than a live device for the same reason
tools/harvest_header_glyphs.py does: the fixtures were captured specifically
to put every currently-visible workshop and cards price on screen at once
(five workshop rows across three tabs, both cards buy buttons), which is a
superset of what one more live capture would add right now. See
tests/test_shopping_prices.py for exactly which digits that does and does not
reach, and the skipped test documenting the gap.

Usage:
    uv run tools/harvest_menu_glyphs.py

Writes unlabelled templates/atlas/menu/glyph_NNN.png files, exactly like
build_atlas.py does, so tools/label_glyphs.py can pick them up unchanged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import digits  # noqa: E402
import vision  # noqa: E402
from device import Image  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# The three workshop tabs. Between them these show every row and unlock tile
# this account currently has: five row-layout prices (Damage, Attack Speed,
# Critical Chance, Critical Factor, Health, Health Regen at 22-23px) and three
# tile-layout prices (the three Unlock tiles at 28-29px).
WORKSHOP_FIXTURES: tuple[str, ...] = (
    "menu_workshop_attack.png",
    "menu_workshop_defense.png",
    "menu_workshop_utility.png",
)
# Both cards buy buttons (x1 and x10), at 31-32px - the largest of the three.
CARDS_FIXTURE = "menu_cards.png"


def _score(a: Image, b: Image) -> float:
    """How alike two already-cropped glyphs are, regardless of which is bigger.

    Always resizes the SMALLER of the two up to the LARGER's shape, never the
    other way round. This is the opposite convention from `digits.Atlas.match`
    (which always resizes the candidate to the stored template) because here
    neither image is "the template" yet - both are just harvested crops, and
    upscaling is the direction that stays reliable in every case measured (see
    the module docstring), so it is the only direction safe to use for
    deciding whether two differently-sized crops are the same glyph.
    """
    if a.size <= b.size:
        small, large = a, b
    else:
        small, large = b, a
    resized = cv2.resize(
        small, (large.shape[1], large.shape[0]), interpolation=cv2.INTER_NEAREST
    )
    return float(cv2.matchTemplate(resized, large, cv2.TM_CCOEFF_NORMED)[0][0])


def _absorb(kept: list[Image], candidate: Image) -> None:
    """Add `candidate` to `kept`, keeping only the taller copy of a duplicate.

    This is what makes "keep the largest instance" a property of the DATA
    rather than of which fixture happened to be processed first: whichever
    order `kept` is built in, the result is the same, because a match always
    keeps whichever of the two glyphs is physically taller and a non-match
    always keeps both. Using `config.GLYPH_MATCH_THRESHOLD` here is
    deliberate, not a repurposing - it is the same "close enough to call it
    the same glyph" boundary the whole rest of this module relies on.
    """
    for index, existing in enumerate(kept):
        if _score(candidate, existing) >= config.GLYPH_MATCH_THRESHOLD:
            if candidate.shape[0] > existing.shape[0]:
                kept[index] = candidate
            return
    kept.append(candidate)


def _harvest_region(
    screen: Image, region: config.Region, anchor: tuple[int, int], kept: list[Image]
) -> None:
    patch = digits.crop(screen, region, anchor)
    if patch is None:
        return
    binary = digits.binarize(patch, digits.threshold_for("menu"))
    for glyph in digits.split_glyphs(binary):
        _absorb(kept, glyph)


def harvest_workshop(screen: Image, cache: vision.TemplateCache, kept: list[Image]) -> None:
    """Every row and unlock-tile price visible on one workshop tab.

    Tries every configured row on every fixture rather than looking up which
    rows belong to which tab: a row absent from this tab simply fails to
    locate (see test_shopping_templates.py's cross-tab test), so trying them
    all is simpler than duplicating that mapping here.
    """
    for template_path, layout in config.WORKSHOP_ROWS.values():
        match = vision.locate_template(screen, cache.get(template_path), 0.9)
        if match is None:
            continue
        _harvest_region(screen, config.PRICE_REGIONS[layout], match.top_left, kept)


def harvest_cards(screen: Image, cache: vision.TemplateCache, kept: list[Image]) -> None:
    """Both cards buy buttons' prices."""
    for template_path in config.CARD_BUTTONS.values():
        match = vision.locate_template(screen, cache.get(template_path), 0.9)
        if match is None:
            continue
        _harvest_region(screen, config.CARD_PRICE_REGION, match.top_left, kept)


def dump_glyphs(kept: list[Image], out_dir: Path) -> list[Path]:
    """Write one PNG per already-deduplicated glyph. Returns the paths written.

    Numbering continues from whatever is already in `out_dir`, so repeated
    calls accumulate instead of overwriting - the same behaviour as
    build_atlas.py's dump_glyphs, which this mirrors so the output is
    indistinguishable to tools/label_glyphs.py.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    start = len(list(out_dir.glob("glyph_*.png")))

    written: list[Path] = []
    for offset, glyph in enumerate(kept):
        path = out_dir / f"glyph_{start + offset:03d}.png"
        cv2.imwrite(str(path), glyph)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fixtures-dir", type=Path, default=FIXTURES_DIR,
        help="directory holding the committed menu_*.png fixtures",
    )
    args = parser.parse_args(argv)

    out_dir = config.ATLAS_DIR / "menu"
    cache = vision.TemplateCache(config.TEMPLATE_DIR)

    kept: list[Image] = []
    for filename in WORKSHOP_FIXTURES:
        path = args.fixtures_dir / filename
        screen = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if screen is None:
            raise SystemExit(f"could not read fixture: {path}")
        harvest_workshop(screen, cache, kept)

    cards_path = args.fixtures_dir / CARDS_FIXTURE
    screen = cv2.imread(str(cards_path), cv2.IMREAD_COLOR)
    if screen is None:
        raise SystemExit(f"could not read fixture: {cards_path}")
    harvest_cards(screen, cache, kept)

    written = dump_glyphs(kept, out_dir)
    print(f"{len(written)} distinct glyphs (each already its largest instance) in {out_dir}")
    print("Now run tools/label_glyphs.py --size-class menu --reference modal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
