"""Jitter on the other two tap paths.

Three modules reach device.tap: tower_bot's purchase loop (covered in
test_tap_jitter.py), navigate.Navigator's RETRY/BATTLE taps, and
shopping.ShoppingSession._tap - which its own docstring pins as "the ONLY
place device.tap is called anywhere in this module", so covering it covers
every workshop tab, workshop row, card buy and exit-to-battle tap at once.

Without these two, the bot would jitter its in-run purchases while still
tapping the dead centre of the same BATTLE button every single run.
"""

from __future__ import annotations

import random
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import pytest

import events
import jitter
import navigate
import vision
from navigate import Navigator
from screens import ScreenState
import shopping as shopping_mod
from strategy import Shopping, ShoppingRule, Strategy

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


def frame(name: str):
    image = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert image is not None, f"missing fixture: {name}.png"
    return image


def a_strategy(**over) -> Strategy:
    return Strategy.from_config().merged(over)


def nav(**over) -> Navigator:
    return Navigator(
        vision.TemplateCache(TEMPLATES), events.EventBus(), **over
    )


def only_tap(dev) -> tuple[int, int]:
    assert dev.click.call_count == 1
    return tuple(dev.click.call_args.args)


# -- navigate ---------------------------------------------------------------
def test_a_nav_tap_is_jittered(monkeypatch: pytest.MonkeyPatch) -> None:
    """BATTLE is tapped once per run, forever. Un-jittered, that is the same
    pixel every run for the life of the bot."""
    monkeypatch.setattr(jitter, "_rng", random.Random(99))
    tuning = a_strategy(tap_jitter_px=8.0, tap_delay=0.0)

    points = set()
    for round_ in range(20):
        dev = MagicMock()
        # A fresh Navigator per round: one instance would gate all but the
        # first tap on its own navigation cooldown.
        nav(cooldown=0.0).maybe_navigate(
            frame("main_menu"), ScreenState.MAIN_MENU, dev, now=0.0, tuning=tuning
        )
        points.add(only_tap(dev))

    assert len(points) > 1, "every BATTLE tap landed on the identical pixel"


def test_zero_jitter_taps_the_template_centre(monkeypatch: pytest.MonkeyPatch) -> None:
    """Back-compat: this is exactly what Navigator did before jitter."""
    dev_off = MagicMock()
    nav(cooldown=0.0).maybe_navigate(
        frame("main_menu"),
        ScreenState.MAIN_MENU,
        dev_off,
        now=0.0,
        tuning=a_strategy(tap_jitter_px=0.0, tap_delay=0.0),
    )

    screen = frame("main_menu")
    match = vision.locate_template(
        screen, vision.TemplateCache(TEMPLATES).get("buttons/battle.png"), 0.8
    )
    assert only_tap(dev_off) == match.center


def test_a_nav_tap_stays_within_the_radius(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jitter, "_rng", random.Random(5))
    screen = frame("main_menu")
    match = vision.locate_template(
        screen, vision.TemplateCache(TEMPLATES).get("buttons/battle.png"), 0.8
    )
    tuning = a_strategy(tap_jitter_px=8.0, tap_delay=0.0)

    for _ in range(20):
        dev = MagicMock()
        nav(cooldown=0.0).maybe_navigate(
            screen, ScreenState.MAIN_MENU, dev, now=0.0, tuning=tuning
        )
        x, y = only_tap(dev)
        assert abs(x - match.center[0]) <= 8
        assert abs(y - match.center[1]) <= 8


def test_a_nav_tap_pauses_before_tapping(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        navigate.jitter, "pause", lambda *a, **k: (order.append("pause"), 0.0)[1]
    )
    dev = MagicMock()
    dev.click.side_effect = lambda x, y: order.append("tap")

    nav(cooldown=0.0).maybe_navigate(
        frame("main_menu"),
        ScreenState.MAIN_MENU,
        dev,
        now=0.0,
        tuning=a_strategy(tap_delay=0.12),
    )

    assert order == ["pause", "tap"]


def test_the_navigation_cooldown_only_ever_stretches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """navigation_cooldown waits out an animation - it is a minimum, like
    click_cooldown. A symmetric jitter would sometimes fire the second tap
    mid-transition, which is the double-navigation the cooldown prevents.

    0.99 of a 1.0s cooldown must stay gated on every roll.
    """
    monkeypatch.setattr(jitter, "_rng", random.Random(3))
    tuning = a_strategy(timing_jitter=0.5, tap_delay=0.0)
    navigator = nav(cooldown=1.0)
    dev = MagicMock()

    navigator.maybe_navigate(
        frame("main_menu"), ScreenState.MAIN_MENU, dev, now=0.0, tuning=tuning
    )
    assert dev.click.call_count == 1

    for _ in range(40):
        navigator.maybe_navigate(
            frame("main_menu"), ScreenState.MAIN_MENU, dev, now=0.99, tuning=tuning
        )

    assert dev.click.call_count == 1


def test_navigate_without_tuning_still_works() -> None:
    """Direct callers (and the existing tests) must keep working without
    passing a policy - same fallback contract as find_and_click_image."""
    dev = MagicMock()

    target = nav(cooldown=0.0).maybe_navigate(
        frame("main_menu"), ScreenState.MAIN_MENU, dev, now=0.0
    )

    assert target == "BATTLE"
    assert dev.click.call_count == 1


# -- shopping ---------------------------------------------------------------
def a_policy(**over) -> Shopping:
    """Armed, with one ATTACK row - the shape bot_on_workshop is built for:
    the row is never on screen, so the session taps the ATTACK tab every
    scan without ever buying or returning."""
    base = dict(
        enabled=True,
        armed=True,
        workshop=(
            ShoppingRule(
                name="Damage", template="workshop/row_damage.png", category="ATTACK"
            ),
        ),
    )
    return Shopping(**{**base, **over})


def visit_taps(bot_on_workshop, **tuning) -> list[tuple[int, int]]:
    """One live visit, advanced twice, returning every tap it sent."""
    policy = a_policy()
    bot = bot_on_workshop(policy)
    bot.controls.apply(tuning)
    bot.shopping.begin(policy, run_count=1)
    bot.run_once()
    bot.run_once()
    return list(bot.device.taps)


def test_a_shopping_tap_is_jittered(
    bot_on_workshop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers every tap shopping makes: _tap is the single choke point, so
    the workshop tab tap exercised here shares its code with every workshop
    row, card buy and exit-to-battle tap."""
    monkeypatch.setattr(jitter, "_rng", random.Random(7))

    points = set()
    for _ in range(12):
        points.update(visit_taps(bot_on_workshop, tap_jitter_px=8.0, tap_delay=0.0))

    assert len(points) > 1, "every shopping tap landed on the identical pixel"


def test_zero_jitter_leaves_shopping_taps_exact(bot_on_workshop) -> None:
    """Back-compat for the shopping path: with jitter off, two identical
    visits must tap identical pixels."""
    first = visit_taps(bot_on_workshop, tap_jitter_px=0.0, tap_delay=0.0)
    second = visit_taps(bot_on_workshop, tap_jitter_px=0.0, tap_delay=0.0)

    assert first, "expected the workshop visit to tap something"
    assert first == second


def test_a_rehearsal_never_pauses(
    bot_on_workshop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """armed=False counts taps against the budget but sends none, so it must
    not pay a pre-tap pause either - a rehearsal exists to be instant."""
    paused: list[float] = []
    monkeypatch.setattr(
        shopping_mod.jitter, "pause", lambda *a, **k: (paused.append(a[0]), 0.0)[1]
    )
    policy = a_policy(armed=False)
    bot = bot_on_workshop(policy)
    bot.controls.apply({"tap_delay": 0.5})
    bot.shopping.begin(policy, run_count=1)

    bot.run_once()
    bot.run_once()

    assert paused == []
    assert bot.device.taps == []
