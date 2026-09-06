from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import cv2
import pytest

import events
import navigate
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


def test_now_none_reads_a_fresh_clock_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitting `now` must sample a live clock on every call, not freeze at a
    fixed value. The buggy `moment = 0.0 if now is None else now` froze
    `moment` at 0.0 forever, so after the first successful tap `self._last`
    also became 0.0 and every later call - real elapsed time notwithstanding
    - saw `moment - self._last == 0.0 < cooldown`, permanently blocking
    navigation. `cooldown=0.0` cannot expose this (0 - 0 < 0.0 is False
    either way), so this test uses a small nonzero cooldown with the clock
    monkeypatched to controlled, increasing values - deterministic, and no
    real sleeping required."""
    dev = MagicMock()
    ticks = iter([100.0, 100.2])
    monkeypatch.setattr(navigate.time, "monotonic", lambda: next(ticks))
    nav2 = Navigator(vision.TemplateCache(TEMPLATES), events.EventBus(), cooldown=0.05)

    assert nav2.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, dev) == "RETRY"
    assert nav2.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, dev) == "RETRY"


def test_retry_is_located_not_hardcoded(nav: Navigator) -> None:
    """The modal moves ~46px between the two game-over fixtures. Locating the
    button by match must find it in both, at different y positions."""
    cache = vision.TemplateCache(TEMPLATES)
    template = cache.get("buttons/retry.png")

    a = vision.locate_template(frame("game_over"), template, 0.8)
    b = vision.locate_template(frame("game_over_fade"), template, 0.8)

    assert a is not None and b is not None
    assert a.center[1] != b.center[1]


# --- The between-runs detour to MAIN_MENU ---------------------------------
# RETRY restarts from the death screen without ever passing through
# MAIN_MENU, and MAIN_MENU is the only screen a Workshop visit can begin
# from. When a visit is due the caller asks for HOME instead.


def test_taps_home_on_game_over_when_a_visit_is_due(nav: Navigator) -> None:
    dev = MagicMock()
    target = nav.maybe_navigate(
        frame("game_over"), ScreenState.GAME_OVER, dev, now=0.0, go_home=True
    )

    assert target == "HOME"
    dev.click.assert_called_once()


def test_home_and_retry_are_different_buttons(nav: Navigator) -> None:
    """Both live on the death screen; tapping one must not land on the other."""
    retry_dev, home_dev = MagicMock(), MagicMock()
    nav.maybe_navigate(frame("game_over"), ScreenState.GAME_OVER, retry_dev, now=0.0)
    nav.maybe_navigate(
        frame("game_over"), ScreenState.GAME_OVER, home_dev, now=0.0, go_home=True
    )

    assert retry_dev.click.call_args != home_dev.click.call_args


def test_go_home_does_not_disturb_the_main_menu(nav: Navigator) -> None:
    """The detour is a GAME_OVER concern. On MAIN_MENU the bot has already
    arrived, and BATTLE stays the only nav button there."""
    dev = MagicMock()
    target = nav.maybe_navigate(
        frame("main_menu"), ScreenState.MAIN_MENU, dev, now=0.0, go_home=True
    )

    assert target == "BATTLE"
