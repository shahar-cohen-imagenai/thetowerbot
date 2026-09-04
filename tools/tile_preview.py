"""Render the tile rectangles find_tiles() detects onto a frame.

Usage:
    uv run tools/tile_preview.py tests/fixtures/menu_workshop_attack.png \
        --out /tmp/tiles.png

Prints every candidate rectangle with its size so you can see which are
outer tiles and which are the value/price panels nested inside them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tiles  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frame", type=Path)
    parser.add_argument("--out", type=Path, default=Path("/tmp/tiles.png"))
    parser.add_argument(
        "--all", action="store_true",
        help="show every candidate, before the size filter",
    )
    args = parser.parse_args(argv)

    screen = cv2.imread(str(args.frame))
    if screen is None:
        print(f"cannot read {args.frame}", file=sys.stderr)
        return 1

    found = tiles.candidates(screen) if args.all else tiles.find_tiles(screen)
    for rect in found:
        print(f"x={rect.x:4d} y={rect.y:4d} w={rect.w:4d} h={rect.h:4d}")
    print(f"{len(found)} rectangles")

    for rect in found:
        cv2.rectangle(
            screen, (rect.x, rect.y),
            (rect.x + rect.w, rect.y + rect.h), (0, 0, 255), 3,
        )
    cv2.imwrite(str(args.out), screen)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
