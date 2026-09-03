"""Harvest header glyphs from the COMMITTED menu fixtures, not a live device.

build_atlas.py's normal path captures from a running emulator, but the live
account's coin balance is frozen at 1.77K while nothing is playing - a fresh
capture would see strictly fewer distinct glyphs than the fixtures already
committed to the repo. Those fixtures were captured across several different
balances (78 coins on two pages, 1.77K on another; 0 and 40 gems), so reading
them is a superset of what one more live capture would add right now.

This intentionally does NOT reach every digit. Between the five fixtures the
coin and gem counters only ever show `0 1 4 7 8 . K` - the account has not
passed through a balance containing `2 3 5 6 9`, and has not run long enough
to hit `M` or `B`. That gap is closed later by build_atlas.py --size-class
header once a real play session pushes the balance through those digits; see
the header atlas completeness tests in tests/test_header_digits.py.

Usage:
    uv run tools/harvest_header_glyphs.py

Writes unlabelled templates/atlas/header/glyph_NNN.png files, exactly like
build_atlas.py does, so tools/label_glyphs.py can pick them up unchanged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import digits  # noqa: E402
import vision  # noqa: E402
from device import Image  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


class FixtureSource(NamedTuple):
    """One committed fixture to read both header counters out of."""

    filename: str
    page: str


# The five fixtures whose headers, between them, cover every glyph this
# account's balances have shown so far. See the module docstring for why
# that is a proper subset of 0-9.
FIXTURES: tuple[FixtureSource, ...] = (
    FixtureSource("menu_workshop_attack.png", "WORKSHOP"),
    FixtureSource("menu_workshop_defense.png", "WORKSHOP"),
    FixtureSource("menu_workshop_utility.png", "WORKSHOP"),
    FixtureSource("menu_cards.png", "CARDS"),
    FixtureSource("menu_main.png", "MAIN_MENU"),
)


def dump_glyphs(
    screen: Image, region: config.Region, anchor: tuple[int, int], out_dir: Path
) -> list[Path]:
    """Write one PNG per glyph found in `region`. Returns the paths written.

    Numbering continues from whatever is already in `out_dir`, so repeated
    calls accumulate instead of overwriting - the same behaviour as
    build_atlas.py's dump_glyphs, which this mirrors so the output is
    indistinguishable to tools/label_glyphs.py.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    start = len(list(out_dir.glob("glyph_*.png")))

    patch = digits.crop(screen, region, anchor)
    if patch is None:
        return []

    binary = digits.binarize(patch, digits.threshold_for("header"))
    written: list[Path] = []
    for offset, glyph in enumerate(digits.split_glyphs(binary)):
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

    out_dir = config.ATLAS_DIR / "header"
    cache = vision.TemplateCache(config.TEMPLATE_DIR)

    total = 0
    for source in FIXTURES:
        path = args.fixtures_dir / source.filename
        screen = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if screen is None:
            raise SystemExit(f"could not read fixture: {path}")

        _, top_left = vision.best_score(screen, cache.get(config.PAGE_ANCHORS[source.page]))
        coins_region, gems_region = config.HEADER_REGIONS[source.page]

        written = 0
        for region in (coins_region, gems_region):
            written += len(dump_glyphs(screen, region, top_left, out_dir))
        total += written
        print(f"{source.filename} ({source.page}): +{written} glyphs ({total} total)")

    print(f"\n{total} glyphs in {out_dir}")
    print("Now run tools/label_glyphs.py --size-class header --reference modal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
