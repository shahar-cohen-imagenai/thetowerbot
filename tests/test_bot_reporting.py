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


# -- FIX 2: snapshot only a CONFIRMED unknown -------------------------------
def unknown_frame():
    """A frame no anchor matches, at the real capture resolution."""
    import numpy as np

    rng = np.random.default_rng(0)
    return rng.integers(0, 256, frame("main_menu").shape, dtype=np.uint8)


def test_the_first_scan_never_snapshots_a_recognisable_screen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The tracker boots at UNKNOWN as a placeholder, so scan 1 of every
    launch used to snapshot whatever was on screen and publish a false
    UnknownScreen. Against a 50-file cap, 50 launches evict every genuine
    snapshot - the trail exists to show what actually confused the bot.
    """
    from snapshots import SnapshotWriter

    bot, rec, _ = make_bot("game_over")
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    bot.snapshots = SnapshotWriter(tmp_path)

    bot.run_once()  # nothing confirmed yet

    assert list(tmp_path.glob("*.png")) == []
    assert rec.of(events.UnknownScreen) == []

    bot.run_once()  # confirms GAME_OVER

    assert list(tmp_path.glob("*.png")) == []
    assert rec.of(events.UnknownScreen) == []


def test_a_confirmed_unknown_screen_is_still_snapshotted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The positive control for the test above: gating on `confirmed` must
    not cost us the diagnostic the spec asks for."""
    from snapshots import SnapshotWriter

    bot, rec, _ = make_bot("main_menu")
    bot._screen = unknown_frame()
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    bot.snapshots = SnapshotWriter(tmp_path)

    bot.run_once()
    assert list(tmp_path.glob("*.png")) == []  # one reading is not enough

    bot.run_once()

    assert len(list(tmp_path.glob("*.png"))) == 1
    assert len(rec.of(events.UnknownScreen)) == 1
    assert bot.screen_state is screens.ScreenState.UNKNOWN
    assert bot.tracker.confirmed is True


# -- FIX 3: failures reach the event stream, not just the log ---------------
def test_an_unexpected_scan_failure_publishes_a_bot_error() -> None:
    """BotError was declared and rendered by both sinks but never published,
    so the log sink's ERROR branch was dead and the TUI could never show a
    failure - under --tui a device error was simply invisible."""
    bot, rec, _ = make_bot("in_run_lit")

    def boom(*_args: object, **_kwargs: object) -> bool:
        bot.stop()
        raise RuntimeError("screencap exploded")

    bot.run_once = boom  # type: ignore[method-assign]
    bot.run_forever(interval=0.0)

    errors = rec.of(events.BotError)
    assert len(errors) == 1
    assert "screencap exploded" in errors[0].message
    assert "RuntimeError" in errors[0].traceback


def test_a_device_failure_publishes_a_bot_error() -> None:
    from device import EmulatorError

    bot, rec, _ = make_bot("in_run_lit")

    def boom(*_args: object, **_kwargs: object) -> bool:
        bot.stop()
        raise EmulatorError("device offline")

    bot.run_once = boom  # type: ignore[method-assign]
    bot.run_forever(interval=0.0)

    errors = rec.of(events.BotError)
    assert len(errors) == 1
    assert "device offline" in errors[0].message
    assert "EmulatorError" in errors[0].traceback


# -- FIX 8: the sink's whole lifetime is inside try/finally -----------------
def test_the_sink_is_closed_when_startup_fails_after_it_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """start() and subscribe() used to run BEFORE the try, with TowerBot
    construction and two signal.signal calls in between. Anything raising in
    that window left the consumer thread running and, under --tui, left
    rich's Live holding the terminal."""
    monkeypatch.setattr(tower_bot, "connect_device", lambda host, port: MagicMock())

    def _explode(**_kwargs: object) -> None:
        raise RuntimeError("template dir missing")

    monkeypatch.setattr(tower_bot, "TowerBot", _explode)

    started: list[bool] = []
    closed: list[bool] = []
    monkeypatch.setattr(tower_bot.LogSink, "start", lambda self: started.append(True))
    monkeypatch.setattr(
        tower_bot.LogSink, "close", lambda self, *a, **k: closed.append(True)
    )

    with pytest.raises(RuntimeError, match="template dir missing"):
        tower_bot.main(["--once"])

    assert started == [True]
    assert closed == [True]
