from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import cv2
import pytest

import events
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
    assert {e.reason for e in rec.of(events.Skipped)} == {"screen_gated"}


def test_main_menu_does_not_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, dev = settled_bot("main_menu", monkeypatch)

    bot.run_once()

    assert rec.of(events.Tapped) == []
    dev.click.assert_not_called()


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
    from affordability import BrightnessAffordability

    check = BrightnessAffordability(ratio=0.75)
    cache = vision.TemplateCache(TEMPLATES)
    template = cache.get("upgrade_damage.png")

    dim = frame("game_over")
    match = vision.locate_template(dim, template, threshold=0.9)
    ok, _ = check.affordable(dim, match, template)
    assert ok is False

    lit = frame("in_run_lit")
    match = vision.locate_template(lit, template, threshold=0.9)
    ok, _ = check.affordable(lit, match, template)
    assert ok is True


def test_cooldown_suppresses_a_repeat_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()

    bot.run_once()
    rec.seen.clear()
    bot.run_once()  # immediately again - all four are still cooling down

    assert rec.of(events.Tapped) == []
    assert {e.reason for e in rec.of(events.Skipped)} == {"cooldown"}
