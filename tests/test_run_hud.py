"""The cash counter as the in-run probe, and as the wallet's anchor.

Two things are being pinned here, and they come from the same observation.

1. A run is a run on every upgrade tab. The upgrade panel's header bar is a
   different crop per tab, so scoring IN_RUN on it read DEFENSE and UTILITY
   as UNKNOWN - the bot going blind for two thirds of a run.
2. The wallet is anchored to the counter rather than to that panel. The
   panel is pinned to the bottom of the screen and the wallet to the top,
   and `in_run_wallet.png` (Android Studio AVD, display cutout) and
   `in_run_wallet_no_cutout.png` (BlueStacks, none) are real captures of the
   same game 136px apart on exactly that axis.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import config
import digits
import run_hud
import screens
import vision

FIXTURES = Path(__file__).parent / "fixtures"

IN_RUN_FIXTURES = [
    "in_run_lit", "in_run_early", "in_run_fast", "in_run_wallet",
    "in_run_paused", "in_run_attack_paused",
    "in_run_defense", "in_run_utility",      # the tabs that used to read UNKNOWN
    "in_run_wallet_no_cutout",               # BlueStacks, no display cutout
]
MENU_FIXTURES = [
    "main_menu", "menu_main", "menu_cards", "menu_workshop",
    "menu_workshop_attack", "menu_workshop_defense", "menu_workshop_utility",
    "menu_missions",
]
GAME_OVER_FIXTURES = [
    "game_over", "game_over_fade", "game_over_newhigh",
    "game_over_stats", "game_over_wave1",
]


@pytest.fixture(scope="module")
def cache() -> vision.TemplateCache:
    return vision.TemplateCache(config.TEMPLATE_DIR)


def load(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"))
    assert img is not None, f"missing fixture: {name}.png"
    return img


# --- the probe ------------------------------------------------------------


@pytest.mark.parametrize("name", IN_RUN_FIXTURES + GAME_OVER_FIXTURES)
def test_cash_counter_is_found_whenever_a_run_is_showing(name, cache) -> None:
    """Game-over frames included: the modal does not cover the HUD."""
    match = run_hud.find_cash(load(name), cache)
    assert match.top_left is not None
    assert match.score >= config.ANCHOR_THRESHOLD


@pytest.mark.parametrize("name", MENU_FIXTURES)
def test_no_cash_counter_outside_a_run(name, cache) -> None:
    """Cash exists only inside a run - that is what makes it the probe."""
    match = run_hud.find_cash(load(name), cache)
    assert match.top_left is None


def test_menu_and_run_scores_are_far_apart(cache) -> None:
    """The threshold must sit in a gap, not on a boundary."""
    lowest_run = min(run_hud.find_cash(load(n), cache).score for n in IN_RUN_FIXTURES)
    highest_menu = max(run_hud.find_cash(load(n), cache).score for n in MENU_FIXTURES)
    assert highest_menu < config.ANCHOR_THRESHOLD < lowest_run
    assert lowest_run - highest_menu > 0.3


def test_search_is_confined_to_the_top_left(cache) -> None:
    """A workshop price carries the same glyph; full-frame would match it."""
    assert config.RUN_CASH_SEARCH.dx == 0 and config.RUN_CASH_SEARCH.dy == 0
    img = load("menu_workshop_attack")
    assert run_hud.find_cash(img, cache).top_left is None


# --- classification -------------------------------------------------------


@pytest.mark.parametrize("name", IN_RUN_FIXTURES)
def test_every_tab_classifies_as_in_run(name, cache) -> None:
    """The regression: DEFENSE and UTILITY used to score 0.46 and 0.33."""
    reading = screens.classify(load(name), cache)
    assert reading.state is screens.ScreenState.IN_RUN
    assert reading.confidence >= config.ANCHOR_THRESHOLD


@pytest.mark.parametrize("name", GAME_OVER_FIXTURES)
def test_game_over_outranks_the_cash_probe(name, cache) -> None:
    """The counter scores ~1.000 behind the modal, so precedence is explicit."""
    reading = screens.classify(load(name), cache)
    assert reading.state is screens.ScreenState.GAME_OVER
    assert reading.cash_top_left is not None  # the probe did fire


def test_menus_are_unaffected(cache) -> None:
    assert screens.classify(load("main_menu"), cache).state is screens.ScreenState.MAIN_MENU
    assert screens.classify(load("menu_cards"), cache).cash_top_left is None


def test_panel_anchor_only_offered_when_it_is_trustworthy(cache) -> None:
    """top_left still means the panel, so speed control keeps its anchor.

    On the two tabs the committed crop does not match, it is withheld rather
    than handed out at 0.33 - every consumer already handles None.
    """
    assert screens.classify(load("in_run_lit"), cache).top_left == (12, 1646)
    assert screens.classify(load("in_run_utility"), cache).top_left is None
    assert screens.classify(load("in_run_defense"), cache).top_left is None


# --- the wallet, off both emulators and all three tabs --------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("in_run_wallet", 86),              # AVD, attack tab
        ("in_run_wallet_no_cutout", 98),    # BlueStacks, attack tab
        ("in_run_utility", 80),
        ("in_run_defense", 80),
    ],
)
def test_wallet_reads_from_the_cash_anchor(name, expected, cache) -> None:
    img = load(name)
    reading = screens.classify(img, cache)
    reader = digits.NumberReader(templates=cache)
    assert reader.read(img, config.WALLET_FROM_CASH, reading.cash_top_left, "wallet") == expected


def test_the_two_emulators_really_do_differ(cache) -> None:
    """Guards the premise. If these ever converge, WALLET_FROM_CASH is moot."""
    avd = screens.classify(load("in_run_wallet"), cache)
    bs = screens.classify(load("in_run_wallet_no_cutout"), cache)
    assert avd.top_left == bs.top_left == (12, 1646)      # bottom cluster: fixed
    assert avd.cash_top_left[1] - bs.cash_top_left[1] == 136   # top cluster: not
    assert avd.cash_top_left[0] == bs.cash_top_left[0]         # no horizontal shift


def test_panel_anchored_wallet_would_have_missed_it(cache) -> None:
    """Why the anchor moved. The old region lands on the gem counter."""
    img = load("in_run_wallet_no_cutout")
    reading = screens.classify(img, cache)
    reader = digits.NumberReader(templates=cache)
    assert reader.read(img, config.WALLET_REGION, (12, 1646), "wallet") is None
    assert reader.read(img, config.WALLET_FROM_CASH, reading.cash_top_left, "wallet") == 98
