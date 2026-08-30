"""The committed atlas against the committed frames. This is the real proof.

Everything in test_digits.py round-trips a synthetic renderer, which proves
the segmentation algorithm and nothing at all about the game's font. This
file is what catches a mislabelled glyph.

Two death-modal fixtures, not one, because the modal has two layouts: a
record run grows a green "New Highest Wave!" line, which pushes Tier and
coins down 49px while the modal's top edge rises 49px as it re-centres. Only
reading both proves the caption anchoring actually holds.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

import config
import digits
import screens
import vision

FIXTURES = Path(__file__).parent / "fixtures"

# Ground truth: what a human read off these frames with their own eyes.
WALLET_FIXTURE = "in_run_wallet.png"
EXPECTED_WALLET = 86

# The four upgrade prices on that same frame, by action name. The price box is
# anchored to the upgrade's own LABEL, not to a screen anchor - each of the
# four buttons has its own box.
EXPECTED_PRICES: dict[str, int] = {
    "Attack Speed": 5,
    "Critical Chance": 4,
    "Damage": 10,
    "Critical Factor": 10,
}

# fixture -> (wave, tier, coins)
MODAL_TRUTH: dict[str, tuple[int, int, int]] = {
    "game_over_stats.png": (1, 1, 0),
    "game_over_newhigh.png": (6, 1, 21),
}


def load(fixture: str) -> np.ndarray:
    screen = cv2.imread(str(FIXTURES / fixture), cv2.IMREAD_COLOR)
    assert screen is not None, f"missing fixture: {fixture}"
    return screen


def anchored(fixture: str, expected_state: str) -> tuple[np.ndarray, tuple[int, int]]:
    screen = load(fixture)
    reading = screens.classify(screen, vision.TemplateCache(config.TEMPLATE_DIR))
    assert reading.state.value == expected_state, (
        f"{fixture} reads as {reading.state.value}, not {expected_state}"
    )
    assert reading.top_left is not None
    return screen, reading.top_left


def test_reads_the_wallet() -> None:
    screen, anchor = anchored(WALLET_FIXTURE, "IN_RUN")
    reader = digits.NumberReader()
    assert reader.read(screen, config.WALLET_REGION, anchor, "wallet") == EXPECTED_WALLET


@pytest.mark.parametrize("action", config.ACTIONS, ids=lambda a: a.name)
def test_reads_every_upgrade_price(action: config.Action) -> None:
    """One offset from the matched label lands on all four price boxes."""
    screen = load(WALLET_FIXTURE)
    label = vision.locate_template(
        screen, vision.TemplateCache(config.TEMPLATE_DIR).get(action.template),
        action.threshold,
    )
    assert label is not None, f"{action.name} did not match its own fixture"

    reader = digits.NumberReader()
    price = reader.read(screen, config.PRICE_REGION, label.top_left, "price")
    assert price == EXPECTED_PRICES[action.name]


@pytest.mark.parametrize("fixture", sorted(MODAL_TRUTH))
def test_reads_the_wave(fixture: str) -> None:
    """Wave holds a fixed offset from the anchor in both layouts."""
    screen, anchor = anchored(fixture, "GAME_OVER")
    reader = digits.NumberReader()
    expected, _, _ = MODAL_TRUTH[fixture]
    assert reader.read(screen, config.MODAL_WAVE_REGION, anchor, "modal") == expected


@pytest.mark.parametrize("fixture", sorted(MODAL_TRUTH))
def test_reads_the_tier(fixture: str) -> None:
    screen, _ = anchored(fixture, "GAME_OVER")
    reader = digits.NumberReader()
    _, expected, _ = MODAL_TRUTH[fixture]
    assert (
        reader.read_at_caption(
            screen, config.MODAL_TIER_CAPTION, config.MODAL_TIER_REGION, "modal"
        )
        == expected
    )


@pytest.mark.parametrize("fixture", sorted(MODAL_TRUTH))
def test_reads_the_coins(fixture: str) -> None:
    screen, _ = anchored(fixture, "GAME_OVER")
    reader = digits.NumberReader()
    _, _, expected = MODAL_TRUTH[fixture]
    assert (
        reader.read_at_caption(
            screen, config.MODAL_COINS_CAPTION, config.MODAL_COINS_REGION, "modal"
        )
        == expected
    )


def test_a_caption_that_is_not_on_screen_reads_none() -> None:
    """The main menu has no death modal. Do not read a number off one."""
    reader = digits.NumberReader()
    assert (
        reader.read_at_caption(
            load("main_menu.png"),
            config.MODAL_COINS_CAPTION,
            config.MODAL_COINS_REGION,
            "modal",
        )
        is None
    )


# Which digits each size class has actually been labelled with. A missing
# digit is SILENT: every number containing it just reads as None, and the bot
# quietly falls back to brightness. These sets are the honest record of what
# has been captured from a live emulator so far.
DIGITS = set("0123456789")
KNOWN_INCOMPLETE: dict[str, str] = {
    "price": "only 0,1,2,4,5 seen so far - needs a live run to capture the rest",
    "modal": "only 0,1,2,6 seen so far - needs deeper runs and higher tiers",
}


@pytest.mark.parametrize(
    "size_class",
    [
        pytest.param(
            name,
            marks=(
                [pytest.mark.xfail(strict=True, reason=KNOWN_INCOMPLETE[name])]
                if name in KNOWN_INCOMPLETE
                else []
            ),
        )
        for name in digits.SIZE_CLASSES
    ],
)
def test_size_class_has_a_full_digit_set(size_class: str) -> None:
    """When one of these XPASSes, delete its KNOWN_INCOMPLETE entry."""
    atlas = digits.AtlasCache().get(size_class)
    assert atlas is not None, f"no atlas for {size_class}"
    missing = DIGITS - atlas.labels
    assert not missing, f"{size_class} atlas is missing {sorted(missing)}"
