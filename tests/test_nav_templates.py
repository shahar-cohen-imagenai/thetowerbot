"""Menu navigation templates, against committed captures of each page.

These are golden data. If one fails, either a template was re-cut badly or the
game's UI moved - both of which would send taps to the wrong place.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import config
import vision

FIXTURES = Path(__file__).parent / "fixtures"
# menu_main, not the older main_menu fixture: that one was captured earlier in
# the game's progression and has no MISSIONS button at all (it scores 0.326
# there). Two valid main menus, so the nav tests use the one the nav templates
# were actually cut from.
PAGE_FIXTURES = {
    "MAIN_MENU": "menu_main",
    "WORKSHOP": "menu_workshop",
    "CARDS": "menu_cards",
    "MISSIONS": "menu_missions",
}


def frame(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    return img


@pytest.mark.parametrize("page,fixture", PAGE_FIXTURES.items())
def test_page_anchor_matches_its_own_page(page: str, fixture: str) -> None:
    screen = frame(fixture)
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    score, _ = vision.best_score(screen, cache.get(config.PAGE_ANCHORS[page]))
    assert score >= 0.9, f"{page} anchor scored only {score:.3f} on its own page"


@pytest.mark.parametrize("page,fixture", PAGE_FIXTURES.items())
def test_page_anchor_rejects_every_other_page(page: str, fixture: str) -> None:
    """Separation is what makes a 0.8 threshold safe. Measured worst case is
    0.592, so this leaves real headroom rather than tracking the current value."""
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    template = cache.get(config.PAGE_ANCHORS[page])
    for other, other_fixture in PAGE_FIXTURES.items():
        if other == page:
            continue
        score, _ = vision.best_score(frame(other_fixture), template)
        assert score < 0.75, f"{page} anchor scored {score:.3f} on {other}"


def test_main_menu_carries_every_nav_button_that_lives_there() -> None:
    screen = frame("menu_main")
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    for name in ("MISSIONS", "WORKSHOP", "CARDS", "BATTLE_TAB"):
        match = vision.locate_template(screen, cache.get(config.NAV_TARGETS[name]), 0.8)
        assert match is not None, f"{name} button not found on the main menu"


def test_missions_page_carries_its_return_button() -> None:
    """Missions is the odd one out: it has no tab, so this is the only way back."""
    screen = frame("menu_missions")
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    match = vision.locate_template(
        screen, cache.get(config.NAV_TARGETS["MISSIONS_RETURN"]), 0.8
    )
    assert match is not None


def test_every_configured_template_exists_on_disk() -> None:
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    for path in (*config.NAV_TARGETS.values(), *config.NAV_DISMISS,
                 *config.PAGE_ANCHORS.values()):
        assert cache.get(path) is not None, f"missing template: {path}"
