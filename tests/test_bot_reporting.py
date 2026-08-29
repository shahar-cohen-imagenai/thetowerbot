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


def make_bot(fixture: str) -> tuple[TowerBot, Recorder, MagicMock]:
    dev = MagicMock()
    bus = events.EventBus()
    rec = Recorder()
    bus.subscribe(rec)
    bot = TowerBot(
        device=dev, templates=vision.TemplateCache(TEMPLATES), bus=bus
    )
    bot._screen = frame(fixture)  # inject the frame; no emulator needed
    return bot, rec, dev


def test_every_scan_publishes_scan_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, _ = make_bot("in_run_lit")
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)

    bot.run_once()

    assert len(rec.of(events.ScanCompleted)) == 1


def test_confirmed_transition_publishes_screen_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot, rec, _ = make_bot("in_run_lit")
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)

    bot.run_once()
    assert rec.of(events.ScreenChanged) == []  # one reading is not enough

    bot.run_once()
    changed = rec.of(events.ScreenChanged)
    assert len(changed) == 1
    assert changed[0].curr == "IN_RUN"
    assert bot.screen_state is screens.ScreenState.IN_RUN
