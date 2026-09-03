"""Menu page classification, against committed captures of each page.

Separate from screens.py on purpose. ScreenState models the RUN lifecycle,
and screens.classify() does ScreenState(winner), which raises on any name the
enum does not have. Menu pages are not run states and must never become enum
members; outside a shopping visit a menu page reading UNKNOWN is correct.
"""

from pathlib import Path

import cv2
import pytest

import config
import pages
import vision

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_FIXTURES = {
    "MAIN_MENU": "menu_main",
    "WORKSHOP": "menu_workshop_attack",
    "CARDS": "menu_cards",
    "MISSIONS": "menu_missions",
}


def frame(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    return img


@pytest.fixture
def cache():
    return vision.TemplateCache(config.TEMPLATE_DIR)


@pytest.mark.parametrize("page,fixture", PAGE_FIXTURES.items())
def test_each_page_classifies_as_itself(page: str, fixture: str, cache) -> None:
    reading = pages.classify_page(frame(fixture), cache)
    assert reading.page == page
    assert reading.confidence >= 0.9
    assert reading.top_left is not None


@pytest.mark.parametrize("page,fixture", PAGE_FIXTURES.items())
def test_no_page_is_confused_for_another(page: str, fixture: str, cache) -> None:
    """Measured worst-case separation is 0.592, so 0.75 leaves real headroom
    rather than tracking the current number."""
    reading = pages.classify_page(frame(fixture), cache)
    others = {name: score for name, score in reading.scores.items() if name != page}
    assert max(others.values()) < 0.75, f"{page} was nearly confused: {others}"


def test_an_in_run_frame_is_not_a_menu_page(cache) -> None:
    """The gate that keeps a shopping visit from starting mid-fight."""
    reading = pages.classify_page(frame("in_run_lit"), cache)
    assert reading.page == pages.UNKNOWN
    assert reading.top_left is None


def test_the_death_modal_is_not_a_menu_page(cache) -> None:
    reading = pages.classify_page(frame("game_over"), cache)
    assert reading.page == pages.UNKNOWN


def test_page_names_never_leak_into_the_run_state_enum() -> None:
    """Regression guard for the exact crash config.py warns about."""
    import screens

    menu_only = set(config.PAGE_ANCHORS) - set(config.SCREEN_ANCHORS)
    assert menu_only, "expected menu pages the run enum does not model"
    for name in menu_only:
        with pytest.raises(ValueError):
            screens.ScreenState(name)
