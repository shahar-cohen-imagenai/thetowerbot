"""Bootstrap the glyph atlas.

Captures frames from the emulator, cuts the configured number regions out of
them, segments those into glyphs, and writes each glyph as an unlabelled
file. You then LABEL them once, by renaming:

    templates/atlas/wallet/glyph_003.png  ->  templates/atlas/wallet/7.png

Punctuation uses names, because a file cannot be called ".png":

    .  ->  dot.png      ,  ->  comma.png      $  ->  dollar.png

Expect 10-30 files per size class before every glyph has been seen. Run it
across a session so large numbers (and their K/M/B suffixes) show up.

    uv run build_atlas.py --size-class wallet --frames 20
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

import config
import digits
import screens
import vision
from device import Image, capture_screen, connect_device

# Which anchor each size class's region is measured from.
REGION_SOURCES: dict[str, tuple[config.Region, str]] = {
    "wallet": (config.WALLET_REGION, "IN_RUN"),
    "modal": (config.MODAL_COINS_REGION, "GAME_OVER"),
}


def dump_glyphs(
    screen: Image, region: config.Region, anchor: tuple[int, int], out_dir: Path
) -> list[Path]:
    """Write one PNG per glyph found in `region`. Returns the paths written.

    Numbering continues from whatever is already in `out_dir`, so repeated
    calls across a session accumulate instead of overwriting.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    start = len(list(out_dir.glob("glyph_*.png")))

    patch = digits.crop(screen, region, anchor)
    if patch is None:
        return []

    written: list[Path] = []
    for offset, glyph in enumerate(digits.split_glyphs(digits.binarize(patch))):
        path = out_dir / f"glyph_{start + offset:03d}.png"
        cv2.imwrite(str(path), glyph)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dump unlabelled glyphs for labelling.")
    parser.add_argument("--size-class", choices=sorted(REGION_SOURCES), required=True)
    parser.add_argument("--frames", type=int, default=20, help="frames to sample")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between frames")
    parser.add_argument("--host", default=config.DEVICE_HOST)
    parser.add_argument("--port", type=int, default=config.DEVICE_PORT)
    args = parser.parse_args(argv)

    region, anchor_name = REGION_SOURCES[args.size_class]
    if region.w == 0 or region.h == 0:
        raise SystemExit(
            f"the {args.size_class} region has not been calibrated yet - "
            "measure it with tools/crop_preview.py and write it into config.py first"
        )

    out_dir = config.ATLAS_DIR / args.size_class
    device = connect_device(args.host, args.port)
    cache = vision.TemplateCache(config.TEMPLATE_DIR)

    total = 0
    for frame in range(args.frames):
        screen = capture_screen(device)
        reading = screens.classify(screen, cache)
        if reading.state.value != anchor_name or reading.top_left is None:
            print(f"frame {frame}: on {reading.state.value}, need {anchor_name} - skipping")
            time.sleep(args.interval)
            continue

        written = dump_glyphs(screen, region, reading.top_left, out_dir)
        total += len(written)
        print(f"frame {frame}: +{len(written)} glyphs ({total} total)")
        time.sleep(args.interval)

    print(f"\n{total} glyphs in {out_dir}")
    print("Now label them by renaming, then delete any duplicates and blanks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
