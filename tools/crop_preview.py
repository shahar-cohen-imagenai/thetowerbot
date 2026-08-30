"""Eyeball a candidate Region against a captured frame.

Usage:
    uv run tools/crop_preview.py tests/fixtures/in_run_wallet.png \
        --anchor IN_RUN --rect 40 -60 220 46 --out /tmp/crop.png

Prints the anchor's match score and where it landed, writes the crop, and
prints the crop's mean grey level so you can tell a real number from an
empty patch of background.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import vision  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frame", type=Path, help="captured PNG to crop from")
    parser.add_argument(
        "--anchor", required=True, choices=sorted(config.SCREEN_ANCHORS),
        help="anchor the rect is measured from",
    )
    parser.add_argument(
        "--rect", nargs=4, type=int, metavar=("DX", "DY", "W", "H"), required=True,
        help="offsets from the anchor's top-left, then width and height",
    )
    parser.add_argument("--out", type=Path, default=Path("crop.png"))
    args = parser.parse_args(argv)

    screen = cv2.imread(str(args.frame), cv2.IMREAD_COLOR)
    if screen is None:
        raise SystemExit(f"could not read frame: {args.frame}")

    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    template = cache.get(config.SCREEN_ANCHORS[args.anchor])
    score, top_left = vision.best_score(screen, template)
    print(f"anchor {args.anchor}: score={score:.3f} top_left={top_left}")
    if score < config.ANCHOR_THRESHOLD:
        print(f"WARNING: below ANCHOR_THRESHOLD ({config.ANCHOR_THRESHOLD}) "
              "- the offsets below are measured from a bad origin")

    dx, dy, w, h = args.rect
    x, y = top_left[0] + dx, top_left[1] + dy
    crop = screen[y : y + h, x : x + w]
    if crop.size == 0:
        raise SystemExit(f"empty crop at ({x},{y}) {w}x{h} - rect is off-screen")

    cv2.imwrite(str(args.out), crop)
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).mean()
    print(f"crop {w}x{h} at absolute ({x},{y}) -> {args.out}  mean_grey={grey:.1f}")
    print(f"config.Region(dx={dx}, dy={dy}, w={w}, h={h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
