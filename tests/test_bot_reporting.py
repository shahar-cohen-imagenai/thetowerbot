from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import cv2
import pytest

import events
import screens
import vision
import tower_bot
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


def test_log_sink_is_closed_even_when_the_scan_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() must close the sink on the way out even if run_once blows up.

    Otherwise an exception mid-scan (e.g. a capture failure) would skip
    log_sink.close() entirely, dropping whatever the sink had buffered.
    """
    monkeypatch.setattr(tower_bot, "connect_device", lambda host, port: MagicMock())
    monkeypatch.setattr(
        tower_bot.TowerBot,
        "run_once",
        lambda self: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    closed: list[bool] = []
    original_close = tower_bot.LogSink.close

    def spy_close(self, *args: object, **kwargs: object) -> None:
        closed.append(True)
        original_close(self, *args, **kwargs)

    monkeypatch.setattr(tower_bot.LogSink, "close", spy_close)

    with pytest.raises(RuntimeError, match="boom"):
        tower_bot.main(["--once"])

    assert closed == [True]
