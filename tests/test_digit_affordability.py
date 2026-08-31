"""Exact affordability, and what it does when a read fails."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

import config
import digits
import vision
from affordability import BrightnessAffordability, DigitAffordability

# screen, match, template, action - the four things affordable() is handed.
Scene = tuple[np.ndarray, vision.Match, np.ndarray, config.Action]


class FakeReader:
    """Returns a scripted price and records what it was asked for."""

    def __init__(self, price: int | None) -> None:
        self.price = price
        self.calls: list[tuple[config.Region, tuple[int, int], str]] = []

    def read(
        self,
        screen: np.ndarray,
        region: config.Region,
        anchor: tuple[int, int],
        size_class: str,
    ) -> int | None:
        self.calls.append((region, anchor, size_class))
        return self.price


@pytest.fixture()
def scene() -> Scene:
    screen = np.full((200, 200, 3), 200, dtype=np.uint8)
    template = np.full((10, 10, 3), 200, dtype=np.uint8)
    match = vision.Match(center=(55, 55), score=1.0, top_left=(50, 50))
    action = config.Action(name="Damage", template="upgrade_damage.png")
    return screen, match, template, action


def test_affordable_when_wallet_covers_the_price(scene: Scene) -> None:
    screen, match, template, action = scene
    check = DigitAffordability(FakeReader(price=100), BrightnessAffordability())
    check.wallet = 250

    ok, _ = check.affordable(screen, match, template, action)

    assert ok is True
    assert check.last_price == 100
    assert check.last_wallet == 250


def test_rejected_when_wallet_is_short(scene: Scene) -> None:
    screen, match, template, action = scene
    check = DigitAffordability(FakeReader(price=500), BrightnessAffordability())
    check.wallet = 250

    ok, detail = check.affordable(screen, match, template, action)

    assert ok is False
    assert "250" in detail and "500" in detail


def test_exactly_enough_is_affordable(scene: Scene) -> None:
    screen, match, template, action = scene
    check = DigitAffordability(FakeReader(price=250), BrightnessAffordability())
    check.wallet = 250
    ok, _ = check.affordable(screen, match, template, action)
    assert ok is True


def test_price_is_read_relative_to_the_matched_label(scene: Scene) -> None:
    """Not from a screen anchor: each upgrade has its own price box."""
    screen, match, template, action = scene
    reader = FakeReader(price=10)
    check = DigitAffordability(reader, BrightnessAffordability())
    check.wallet = 100

    check.affordable(screen, match, template, action)

    region, anchor, size_class = reader.calls[0]
    assert region == config.PRICE_REGION
    assert anchor == match.top_left
    assert size_class == "price"


def test_unreadable_price_falls_back_to_brightness(scene: Scene) -> None:
    screen, match, template, action = scene
    check = DigitAffordability(FakeReader(price=None), BrightnessAffordability())
    check.wallet = 250

    ok, _ = check.affordable(screen, match, template, action)

    assert ok is True  # brightness ratio is 1.0 on this uniform scene
    assert check.last_price is None


def test_unknown_wallet_falls_back_to_brightness(scene: Scene) -> None:
    _, match, template, action = scene
    dim = np.full((200, 200, 3), 40, dtype=np.uint8)
    check = DigitAffordability(FakeReader(price=10), BrightnessAffordability())
    check.wallet = None

    ok, detail = check.affordable(dim, match, template, action)

    assert ok is False  # the brightness gate still catches the dimmed modal
    assert "brightness" in detail


def test_brightness_check_reports_no_price() -> None:
    """The protocol's price fields exist on both implementations."""
    check = BrightnessAffordability()
    assert check.last_price is None
    assert check.last_wallet is None


# --- Against a real frame ---------------------------------------------------
# Everything above scripts the reader. These two run the whole path - locate
# the label, crop its price box, segment, match the committed atlas, compare -
# over the same fixture test_atlas_real.py reads $86 and a $10 Damage off.

FIXTURES = Path(__file__).parent / "fixtures"
REAL_WALLET = 86
DEAREST_PRICE = 10


def damage_label() -> tuple[np.ndarray, vision.Match, np.ndarray, config.Action]:
    screen = cv2.imread(str(FIXTURES / "in_run_wallet.png"), cv2.IMREAD_COLOR)
    assert screen is not None
    action = next(a for a in config.ACTIONS if a.name == "Damage")
    template = vision.TemplateCache(config.TEMPLATE_DIR).get(action.template)
    match = vision.locate_template(screen, template, action.threshold)
    assert match is not None
    return screen, match, template, action


def test_a_real_wallet_covers_a_real_price() -> None:
    screen, match, template, action = damage_label()
    check = DigitAffordability(digits.NumberReader())
    check.wallet = REAL_WALLET

    ok, _ = check.affordable(screen, match, template, action)

    assert check.last_price == DEAREST_PRICE, "the price was never read at all"
    assert ok is True


def test_a_real_price_out_of_reach_is_rejected() -> None:
    """The frame is lit, so brightness alone would have tapped it."""
    screen, match, template, action = damage_label()
    check = DigitAffordability(digits.NumberReader())
    check.wallet = DEAREST_PRICE - 1

    ok, detail = check.affordable(screen, match, template, action)

    assert ok is False
    assert detail == f"wallet {DEAREST_PRICE - 1} < price {DEAREST_PRICE}"
