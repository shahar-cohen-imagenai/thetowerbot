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

One size class per run, and each one wants a different part of the game on
screen: `wallet` and `price` sample an in-run frame, `modal` samples the death
modal. Nothing is captured while the wrong screen is up, so start a run before
harvesting the first two and let it end before harvesting the third.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import cv2

import config
import digits
import screens
import vision
from device import Image, capture_screen, connect_device

# How one number's region is anchored on a frame. Returns EVERY anchor the
# frame offers, because a single frame may hold several instances of the same
# number size - the four upgrade prices, for one.
AnchorFinder = Callable[
    [Image, screens.ScreenReading, vision.TemplateCache], list[tuple[int, int]]
]


def screen_anchor(
    screen: Image, reading: screens.ScreenReading, cache: vision.TemplateCache
) -> list[tuple[int, int]]:
    """The matched screen anchor itself - for numbers at a fixed offset."""
    return [] if reading.top_left is None else [reading.top_left]


def caption_anchor(template: str) -> AnchorFinder:
    """The number's own caption - for numbers that move with the layout.

    Tier and coins shift 49px down on a record run while the modal's top edge
    rises as it re-centres, so neither holds a fixed offset from the screen
    anchor.
    """

    def find(
        screen: Image, reading: screens.ScreenReading, cache: vision.TemplateCache
    ) -> list[tuple[int, int]]:
        match = vision.locate_template(
            screen, cache.get(template), config.ANCHOR_THRESHOLD
        )
        return [] if match is None else [match.top_left]

    return find


def upgrade_labels(
    screen: Image, reading: screens.ScreenReading, cache: vision.TemplateCache
) -> list[tuple[int, int]]:
    """Every upgrade label on the frame - each has its own price box.

    The price region is measured from the LABEL, not from a screen anchor,
    so this yields four anchors per in-run frame rather than one.
    """
    anchors: list[tuple[int, int]] = []
    for action in config.ACTIONS:
        match = vision.locate_template(
            screen, cache.get(action.template), action.threshold
        )
        if match is not None:
            anchors.append(match.top_left)
    return anchors


class Source(NamedTuple):
    """One number to harvest glyphs from: which screen, where, how anchored."""

    state: str
    region: config.Region
    anchors: AnchorFinder = screen_anchor


# Every place each size class's glyphs can be collected from. A size class
# with no source here cannot be filled in at all, and the gap that leaves in
# the atlas is silent: any number containing the missing digit reads as None.
SOURCES: dict[str, tuple[Source, ...]] = {
    "wallet": (Source("IN_RUN", config.WALLET_REGION),),
    "price": (Source("IN_RUN", config.PRICE_REGION, upgrade_labels),),
    # All three modal lines render at the same size. Sampling only one of them
    # would need far more deaths to see every digit.
    "modal": (
        Source("GAME_OVER", config.MODAL_WAVE_REGION),
        Source(
            "GAME_OVER",
            config.MODAL_TIER_REGION,
            caption_anchor(config.MODAL_TIER_CAPTION),
        ),
        Source(
            "GAME_OVER",
            config.MODAL_COINS_REGION,
            caption_anchor(config.MODAL_COINS_CAPTION),
        ),
    ),
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
    parser.add_argument("--size-class", choices=sorted(SOURCES), required=True)
    parser.add_argument("--frames", type=int, default=20, help="frames to sample")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between frames")
    parser.add_argument("--host", default=config.DEVICE_HOST)
    parser.add_argument("--port", type=int, default=config.DEVICE_PORT)
    args = parser.parse_args(argv)

    sources = SOURCES[args.size_class]
    if any(source.region.w == 0 or source.region.h == 0 for source in sources):
        raise SystemExit(
            f"a {args.size_class} region has not been calibrated yet - "
            "measure it with tools/crop_preview.py and write it into config.py first"
        )
    wanted = sorted({source.state for source in sources})

    out_dir = config.ATLAS_DIR / args.size_class
    device = connect_device(args.host, args.port)
    cache = vision.TemplateCache(config.TEMPLATE_DIR)

    total = 0
    for frame in range(args.frames):
        screen = capture_screen(device)
        reading = screens.classify(screen, cache)
        applicable = [s for s in sources if s.state == reading.state.value]
        if not applicable:
            print(
                f"frame {frame}: on {reading.state.value}, "
                f"need {'/'.join(wanted)} - skipping"
            )
            time.sleep(args.interval)
            continue

        written = 0
        for source in applicable:
            for anchor in source.anchors(screen, reading, cache):
                written += len(dump_glyphs(screen, source.region, anchor, out_dir))
        total += written
        print(f"frame {frame}: +{written} glyphs ({total} total)")
        time.sleep(args.interval)

    print(f"\n{total} glyphs in {out_dir}")
    print("Now label them by renaming, then delete any duplicates and blanks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
