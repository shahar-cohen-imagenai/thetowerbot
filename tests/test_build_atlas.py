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
    written = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)

    assert len(written) == 3
    assert all(p.exists() for p in written)
    assert sorted(p.name for p in written) == [
        "glyph_000.png", "glyph_001.png", "glyph_002.png"
    ]


def test_dumped_glyphs_are_readable_greyscale(tmp_path: Path) -> None:
    screen, region = scene("5")
    written = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)
    img = cv2.imread(str(written[0]), cv2.IMREAD_GRAYSCALE)
    assert img is not None and img.size > 0


def test_does_not_overwrite_existing_numbering(tmp_path: Path) -> None:
    """Called twice over a session, the second batch must not clobber the first."""
    screen, region = scene("12")
    build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)
    second = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)

    assert len(list(tmp_path.glob("glyph_*.png"))) == 4
    assert second[0].name == "glyph_002.png"


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
