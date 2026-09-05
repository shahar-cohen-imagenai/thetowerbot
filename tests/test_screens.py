from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import config
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


@pytest.mark.parametrize(
    "fixture,expected",
    [("main_menu", "MAIN_MENU"), ("in_run_lit", "IN_RUN"), ("game_over", "GAME_OVER")],
)
def test_winning_anchor_beats_the_runner_up_by_a_wide_margin(
    cache: vision.TemplateCache, fixture: str, expected: str
) -> None:
    """A correctness-only test would still pass with a badly re-cut anchor.
    Asserting the margin is what actually guards the thresholds.

    Scored against SCREEN_ANCHORS directly rather than through classify:
    classify reports IN_RUN's score from the cash counter, and that fires on
    game-over frames too by design - the death modal does not cover the HUD -
    so the three numbers classify returns are deliberately no longer mutually
    exclusive. The anchor templates still are, and they are what this test
    was written to guard. The cash probe's own margin is asserted in
    test_run_hud.py::test_menu_and_run_scores_are_far_apart.
    """
    img = frame(fixture)
    scores = {
        name: vision.best_score(img, cache.get(path))[0]
        for name, path in config.SCREEN_ANCHORS.items()
    }

    assert scores[expected] >= 0.8, f"winner {scores[expected]:.3f} below threshold"
    runner_up = max(score for name, score in scores.items() if name != expected)
    assert runner_up <= 0.5, f"runner-up {runner_up:.3f} too close"


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
