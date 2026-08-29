from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import screens
import vision

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


@pytest.fixture
def cache() -> vision.TemplateCache:
    return vision.TemplateCache(TEMPLATES)


def frame(name: str):
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("main_menu", screens.ScreenState.MAIN_MENU),
        ("in_run_lit", screens.ScreenState.IN_RUN),
        ("game_over", screens.ScreenState.GAME_OVER),
    ],
)
def test_classifies_each_fixture(
    cache: vision.TemplateCache, fixture: str, expected: screens.ScreenState
) -> None:
    assert screens.classify(frame(fixture), cache).state is expected


@pytest.mark.parametrize("fixture", ["main_menu", "in_run_lit", "game_over"])
def test_winning_anchor_beats_the_runner_up_by_a_wide_margin(
    cache: vision.TemplateCache, fixture: str
) -> None:
    """A correctness-only test would still pass with a badly re-cut anchor.
    Asserting the margin is what actually guards the thresholds."""
    reading = screens.classify(frame(fixture), cache)
    ranked = sorted(reading.scores.values(), reverse=True)

    assert ranked[0] >= 0.8, f"winner {ranked[0]:.3f} below threshold"
    assert ranked[1] <= 0.5, f"runner-up {ranked[1]:.3f} too close"


def test_unrecognised_frame_is_unknown(cache: vision.TemplateCache) -> None:
    blank = frame("main_menu").copy()
    blank[:, :] = 0

    reading = screens.classify(blank, cache)

    assert reading.state is screens.ScreenState.UNKNOWN


def test_reading_reports_every_anchor_score(cache: vision.TemplateCache) -> None:
    reading = screens.classify(frame("in_run_lit"), cache)
    assert set(reading.scores) == {"MAIN_MENU", "IN_RUN", "GAME_OVER"}


def test_game_over_reading_carries_the_anchor_position(
    cache: vision.TemplateCache,
) -> None:
    """Death-modal regions are anchor-relative because the modal moves ~46px."""
    reading = screens.classify(frame("game_over"), cache)
    assert reading.top_left is not None
