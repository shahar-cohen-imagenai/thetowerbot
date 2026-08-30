from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import cv2
import pytest

import events
import screens
import vision
from tower_bot import TowerBot

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


class Recorder:
    def __init__(self) -> None:
        self.seen: list[events.Event] = []

    def offer(self, event: events.Event) -> bool:
        self.seen.append(event)
        return True

    def of(self, kind: type) -> list[events.Event]:
        return [e for e in self.seen if isinstance(e, kind)]


def frame(name: str):
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


def settled_bot(fixture: str, monkeypatch: pytest.MonkeyPatch):
    """A bot whose tracker has already confirmed the fixture's screen."""
    dev = MagicMock()
    bus = events.EventBus()
    rec = Recorder()
    bus.subscribe(rec)
    bot = TowerBot(device=dev, templates=vision.TemplateCache(TEMPLATES), bus=bus)
    bot._screen = frame(fixture)
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    bot.run_once()  # first reading
    bot.run_once()  # confirms the transition
    rec.seen.clear()
    return bot, rec, dev


def test_game_over_does_not_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the originating bug.

    The upgrade panel is still rendered behind the death modal and the
    templates score 1.000 against it. Asserting the ABSENCE of a tap is the
    point. The reason is screen_gated, because the screen gate fires before
    the brightness check ever runs - see test_brightness_gate_rejects_dimmed
    for direct coverage of the gate itself.
    """
    bot, rec, dev = settled_bot("game_over", monkeypatch)

    bot.run_once()

    assert rec.of(events.Tapped) == []
    dev.click.assert_not_called()

    # ONE event for the whole scan, not one per action. At the 2s default
    # that is the difference between ~43k and ~172k events a day.
    skips = rec.of(events.Skipped)
    assert [e.reason for e in skips] == ["screen_gated"]
    assert skips[0].detail == "screen is GAME_OVER"


def test_main_menu_does_not_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, dev = settled_bot("main_menu", monkeypatch)

    bot.run_once()

    assert rec.of(events.Tapped) == []
    dev.click.assert_not_called()
    assert [e.reason for e in rec.of(events.Skipped)] == ["screen_gated"]


def test_in_run_taps_every_affordable_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()  # ignore cooldown from the settling scans

    bot.run_once()

    tapped = {e.action for e in rec.of(events.Tapped)}
    assert len(tapped) == 4


def test_brightness_gate_rejects_dimmed_regions() -> None:
    """Direct coverage of BrightnessAffordability, which the full-loop
    game-over test never reaches because screen gating fires first."""
    import config
    from affordability import BrightnessAffordability

    check = BrightnessAffordability()
    action = config.Action(
        name="Damage", template="upgrade_damage.png", threshold=0.9,
        brightness_ratio=0.75,
    )
    cache = vision.TemplateCache(TEMPLATES)
    template = cache.get(action.template)

    dim = frame("game_over")
    match = vision.locate_template(dim, template, threshold=0.9)
    ok, _ = check.affordable(dim, match, template, action)
    assert ok is False

    lit = frame("in_run_lit")
    match = vision.locate_template(lit, template, threshold=0.9)
    ok, _ = check.affordable(lit, match, template, action)
    assert ok is True


def test_a_per_action_brightness_ratio_of_zero_disables_the_check() -> None:
    """README documents `brightness_ratio` as a per-button knob, so it has to
    actually reach the affordability check - it was dead config."""
    import config
    from affordability import BrightnessAffordability

    check = BrightnessAffordability()
    cache = vision.TemplateCache(TEMPLATES)
    template = cache.get("upgrade_damage.png")
    dim = frame("game_over")
    match = vision.locate_template(dim, template, threshold=0.9)

    gated = config.Action(name="Damage", template="upgrade_damage.png",
                          brightness_ratio=0.75)
    assert check.affordable(dim, match, template, gated)[0] is False

    disabled = config.Action(name="Damage", template="upgrade_damage.png",
                             brightness_ratio=0.0)
    assert check.affordable(dim, match, template, disabled)[0] is True


def test_a_per_action_brightness_ratio_reaches_the_scan_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: raising one action's ratio above what the lit frame
    measures must skip that action and only that action."""
    import config

    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()
    monkeypatch.setattr(config, "ACTIONS", (
        config.Action(name="Damage", template="upgrade_damage.png",
                      threshold=0.9, brightness_ratio=0.75),
        config.Action(name="Critical Chance", template="upgrade_critical_chance.png",
                      threshold=0.9, brightness_ratio=99.0),
    ))

    bot.run_once()

    assert [e.action for e in rec.of(events.Tapped)] == ["upgrade_damage.png"]
    skipped = rec.of(events.Skipped)
    assert [(e.action, e.reason) for e in skipped] == [
        ("upgrade_critical_chance.png", "dimmed")
    ]


def test_gating_uses_the_debounced_state_not_the_raw_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate must read the tracker, not the frame in front of it.

    Every other gating test scans one static fixture twice, so the raw
    reading and the confirmed state always agree and an implementation
    gating on the raw reading would pass them all. Debounced gating is the
    spec's defence against a fade frame that still scores as IN_RUN, so it
    needs a test where the two disagree.
    """
    bot, rec, dev = settled_bot("game_over", monkeypatch)
    bot._last_click.clear()

    bot._screen = frame("in_run_lit")  # raw reading flips; tracker has not
    bot.run_once()

    assert bot.screen_state is screens.ScreenState.GAME_OVER
    assert rec.of(events.Tapped) == []
    dev.click.assert_not_called()
    assert [e.reason for e in rec.of(events.Skipped)] == ["screen_gated"]

    # Positive control: the second identical frame confirms IN_RUN, and only
    # then does the bot tap. Otherwise this test would pass against a bot
    # that never taps at all.
    bot.run_once()

    assert bot.screen_state is screens.ScreenState.IN_RUN
    assert len(rec.of(events.Tapped)) == 4


def test_cooldown_suppresses_a_repeat_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()

    bot.run_once()
    rec.seen.clear()
    bot.run_once()  # immediately again - all four are still cooling down

    assert rec.of(events.Tapped) == []
    assert {e.reason for e in rec.of(events.Skipped)} == {"cooldown"}
