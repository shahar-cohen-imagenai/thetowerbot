from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import vision

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


@pytest.fixture
def cache() -> vision.TemplateCache:
    return vision.TemplateCache(TEMPLATES)


def frame(name: str):
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


def test_locate_finds_a_lit_upgrade_in_a_live_run(cache: vision.TemplateCache) -> None:
    match = vision.locate_template(
        frame("in_run_lit"), cache.get("upgrade_damage.png"), threshold=0.9
    )
    assert match is not None
    assert match.score > 0.99


def test_locate_returns_none_below_threshold(cache: vision.TemplateCache) -> None:
    """The upgrade panel is absent from the main menu."""
    match = vision.locate_template(
        frame("main_menu"), cache.get("upgrade_damage.png"), threshold=0.9
    )
    assert match is None


def test_score_alone_cannot_separate_in_run_from_game_over(
    cache: vision.TemplateCache,
) -> None:
    """The core reason the brightness gate exists.

    The upgrade panel is still rendered behind the death modal, and
    TM_CCOEFF_NORMED normalises out brightness - so the template scores ~1.0
    on BOTH screens. Only brightness separates them.
    """
    template = cache.get("upgrade_damage.png")
    in_run = vision.locate_template(frame("in_run_lit"), template, threshold=0.9)
    game_over = vision.locate_template(frame("game_over"), template, threshold=0.9)

    assert in_run is not None and game_over is not None
    assert in_run.score > 0.99
    assert game_over.score > 0.99  # identical score, different screen


def test_brightness_ratio_separates_lit_from_dimmed(
    cache: vision.TemplateCache,
) -> None:
    """Measured: 1.00 lit, 0.28 dimmed. The 0.75 threshold sits between."""
    template = cache.get("upgrade_damage.png")

    lit_frame = frame("in_run_lit")
    lit_match = vision.locate_template(lit_frame, template, threshold=0.9)
    lit = vision.brightness_ratio(lit_frame, lit_match, template)

    dim_frame = frame("game_over")
    dim_match = vision.locate_template(dim_frame, template, threshold=0.9)
    dim = vision.brightness_ratio(dim_frame, dim_match, template)

    assert lit == pytest.approx(1.0, abs=0.05)
    assert dim < 0.4
    assert dim < 0.75 < lit


def test_cache_returns_the_same_object_twice(cache: vision.TemplateCache) -> None:
    assert cache.get("upgrade_damage.png") is cache.get("upgrade_damage.png")


def test_missing_template_raises(cache: vision.TemplateCache) -> None:
    with pytest.raises(FileNotFoundError):
        cache.get("does_not_exist.png")
