from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import cv2
import pytest

import events
import vision
from navigate import Navigator
from screens import ScreenState

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


def frame(name: str):
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


@pytest.fixture
def nav() -> Navigator:
    return Navigator(vision.TemplateCache(TEMPLATES), events.EventBus())


def test_taps_retry_on_game_over(nav: Navigator) -> None:
    dev = MagicMock()
    target = nav.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, dev, now=0.0)

    assert target == "RETRY"
    dev.click.assert_called_once()


def test_taps_battle_on_the_main_menu(nav: Navigator) -> None:
    dev = MagicMock()
    target = nav.maybe_navigate(frame("main_menu"), ScreenState.MAIN_MENU, dev, now=0.0)

    assert target == "BATTLE"
    dev.click.assert_called_once()


def test_does_nothing_in_run(nav: Navigator) -> None:
    dev = MagicMock()
    assert nav.maybe_navigate(frame("in_run_lit"), ScreenState.IN_RUN, dev, now=0.0) is None
    dev.click.assert_not_called()


def test_cooldown_prevents_a_double_tap(nav: Navigator) -> None:
    dev = MagicMock()
    assert nav.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, dev, now=0.0)
    assert nav.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, dev, now=1.0) is None
    assert nav.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, dev, now=4.0)


def test_retry_is_located_not_hardcoded(nav: Navigator) -> None:
    """The modal moves ~46px between the two game-over fixtures. Locating the
    button by match must find it in both, at different y positions."""
    cache = vision.TemplateCache(TEMPLATES)
    template = cache.get("buttons/retry.png")

    a = vision.locate_template(frame("game_over"), template, 0.8)
    b = vision.locate_template(frame("game_over_fade"), template, 0.8)

    assert a is not None and b is not None
    assert a.center[1] != b.center[1]
