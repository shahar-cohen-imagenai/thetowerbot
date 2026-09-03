"""The atlas bootstrap tool. Labelling is manual; segmentation is not."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

import build_atlas
import config
import digits
import screens
import vision
from tests.test_digits import render_text  # needs tests/__init__.py

FIXTURES = Path(__file__).parent / "fixtures"


def scene(text: str) -> tuple[np.ndarray, config.Region]:
    rendered = render_text(text)
    screen = np.zeros((200, 400, 3), dtype=np.uint8)
    screen[0 : rendered.shape[0], 0 : rendered.shape[1]] = rendered
    return screen, config.Region(dx=0, dy=0, w=rendered.shape[1], h=rendered.shape[0])


def test_dumps_one_file_per_glyph(tmp_path: Path) -> None:
    screen, region = scene("407")
    written = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path, "wallet")

    assert len(written) == 3
    assert all(p.exists() for p in written)
    assert sorted(p.name for p in written) == [
        "glyph_000.png", "glyph_001.png", "glyph_002.png"
    ]


def test_dumped_glyphs_are_readable_greyscale(tmp_path: Path) -> None:
    screen, region = scene("5")
    written = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path, "wallet")
    img = cv2.imread(str(written[0]), cv2.IMREAD_GRAYSCALE)
    assert img is not None and img.size > 0


def test_does_not_overwrite_existing_numbering(tmp_path: Path) -> None:
    """Called twice over a session, the second batch must not clobber the first."""
    screen, region = scene("12")
    build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path, "wallet")
    second = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path, "wallet")

    assert len(list(tmp_path.glob("glyph_*.png"))) == 4
    assert second[0].name == "glyph_002.png"


def test_header_source_binarises_at_the_header_threshold(tmp_path: Path) -> None:
    """dump_glyphs must resolve ITS OWN threshold from the size class it was
    given, not fall back to the bare (dark-panel) default. The header bar is
    light, not dark: at config.DIGIT_BINARY_THRESHOLD the panel survives
    binarisation and bridges adjacent glyphs, so "1.77K" segments as
    1 . 77 K (4 files) instead of 1 . 7 7 K (5 files) - a merged span that
    can never be labelled. This is a behavioural check, not a check of the
    threshold constant: it fails again if dump_glyphs ever goes back to a
    bare digits.binarize(patch) call, whatever the constant's value is.
    """
    screen = frame("menu_workshop_attack.png")
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    _, top_left = vision.best_score(screen, cache.get(config.PAGE_ANCHORS["WORKSHOP"]))
    coins_region, _ = config.HEADER_REGIONS["WORKSHOP"]

    written = build_atlas.dump_glyphs(screen, coins_region, top_left, tmp_path, "header")

    assert len(written) == 5, "1.77K must split into 1 . 7 7 K, not merge the two 7s"


# --- Where glyphs are harvested from ---------------------------------------


def frame(name: str) -> np.ndarray:
    screen = cv2.imread(str(FIXTURES / name), cv2.IMREAD_COLOR)
    assert screen is not None, f"missing fixture: {name}"
    return screen


def anchors_for(source: build_atlas.Source, fixture: str) -> list[tuple[int, int]]:
    screen = frame(fixture)
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    return source.anchors(screen, screens.classify(screen, cache), cache)


def test_every_size_class_can_be_harvested() -> None:
    """A size class with no source cannot be filled in at all, and the atlas
    gap that leaves is silent: numbers using a missing digit just read None."""
    assert set(build_atlas.SOURCES) == set(digits.SIZE_CLASSES)


def test_price_is_anchored_on_each_upgrade_label() -> None:
    """Four buttons, four price boxes, one shared offset. Sampling only one
    of them wastes three quarters of every frame."""
    (source,) = build_atlas.SOURCES["price"]
    assert len(anchors_for(source, "in_run_wallet.png")) == len(config.ACTIONS)


def test_no_upgrade_labels_off_the_run_screen() -> None:
    (source,) = build_atlas.SOURCES["price"]
    assert anchors_for(source, "main_menu.png") == []


def test_modal_harvests_wave_tier_and_coins() -> None:
    """All three render at the same size. Only sampling all three gets a full
    digit set out of a session in reasonable time."""
    regions = {source.region for source in build_atlas.SOURCES["modal"]}
    assert regions == {
        config.MODAL_WAVE_REGION,
        config.MODAL_TIER_REGION,
        config.MODAL_COINS_REGION,
    }


def test_every_modal_source_anchors_on_the_death_modal() -> None:
    for source in build_atlas.SOURCES["modal"]:
        assert anchors_for(source, "game_over_newhigh.png"), f"{source.region} found nothing"


def test_a_caption_anchor_finds_nothing_when_the_caption_is_absent() -> None:
    find = build_atlas.caption_anchor(config.MODAL_COINS_CAPTION)
    screen = frame("main_menu.png")
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    assert find(screen, screens.classify(screen, cache), cache) == []
