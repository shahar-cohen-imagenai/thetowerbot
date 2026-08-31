from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import digits
import tower_bot
from affordability import BrightnessAffordability, DigitAffordability
from tests.test_digits import build_synthetic_atlas
from tower_bot import parse_args


def test_auto_navigate_defaults_off() -> None:
    """A bot that launches battles the moment it starts is surprising."""
    assert parse_args([]).auto_navigate is False


def test_auto_navigate_can_be_enabled() -> None:
    assert parse_args(["--auto-navigate"]).auto_navigate is True


def test_tui_defaults_off() -> None:
    assert parse_args([]).tui is False


def test_max_runs_defaults_to_unlimited() -> None:
    assert parse_args([]).max_runs is None


def test_max_runs_parses_an_integer() -> None:
    assert parse_args(["--max-runs", "5"]).max_runs == 5


def test_run_forever_stops_at_max_runs(monkeypatch) -> None:
    """--max-runs is meaningless unless the loop actually honours it."""
    import cv2
    import events
    import vision
    from tower_bot import TowerBot

    fixtures = Path(__file__).parent / "fixtures"
    bot = TowerBot(
        device=MagicMock(),
        templates=vision.TemplateCache(Path(__file__).parent.parent / "templates"),
        bus=events.EventBus(),
    )
    bot._screen = cv2.imread(str(fixtures / "main_menu.png"), cv2.IMREAD_COLOR)
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    monkeypatch.setattr("time.sleep", lambda _: None)
    bot.runs.completed = 3

    # RULING 1: the check must happen before run_once() is ever called, not
    # merely before the *second* call. Spy on run_once so a check placed
    # after the scan (which would still "return immediately" after exactly
    # one extra scan) cannot slip past this test.
    scans: list[bool] = []
    monkeypatch.setattr(bot, "run_once", lambda: scans.append(True))

    bot.run_forever(interval=0.0, max_runs=3)  # must return immediately

    assert scans == []


def test_once_settles_the_tracker(monkeypatch) -> None:
    """RULING 2: --once must run enough scans for the debounced tracker to
    confirm a state, or it can never report the real screen (a single scan
    always leaves ScreenTracker at UNKNOWN - confirmed against a live
    emulator, which read GAME_OVER at 0.998 while --once printed UNKNOWN)."""
    import cv2
    import screens
    import tower_bot

    fixtures = Path(__file__).parent / "fixtures"
    frame = cv2.imread(str(fixtures / "game_over.png"), cv2.IMREAD_COLOR)

    captured: dict[str, tower_bot.TowerBot] = {}
    real_init = tower_bot.TowerBot.__init__

    def spy_init(self, *args, **kwargs) -> None:
        real_init(self, *args, **kwargs)
        captured["bot"] = self

    monkeypatch.setattr(tower_bot, "connect_device", lambda host, port: MagicMock())
    monkeypatch.setattr(tower_bot, "capture_screen", lambda device: frame)
    monkeypatch.setattr(tower_bot.TowerBot, "__init__", spy_init)

    exit_code = tower_bot.main(["--once"])

    assert exit_code == 0
    assert captured["bot"].screen_state is screens.ScreenState.GAME_OVER
    assert captured["bot"].screen_state is not screens.ScreenState.UNKNOWN


def test_debug_scores_prints_a_table_and_exits_without_scanning(
    monkeypatch, capsys
) -> None:
    """RULING 3: --debug-scores is a one-shot diagnostic - one capture, one
    table of every action and screen-anchor score, no scan loop, no taps."""
    import cv2
    import config
    import tower_bot

    fixtures = Path(__file__).parent / "fixtures"
    frame = cv2.imread(str(fixtures / "main_menu.png"), cv2.IMREAD_COLOR)

    monkeypatch.setattr(tower_bot, "connect_device", lambda host, port: MagicMock())
    monkeypatch.setattr(tower_bot, "capture_screen", lambda device: frame)

    def _boom(*args, **kwargs):
        raise AssertionError("TowerBot must not be constructed in --debug-scores mode")

    monkeypatch.setattr(tower_bot, "TowerBot", _boom)

    exit_code = tower_bot.main(["--debug-scores"])

    assert exit_code == 0
    out = capsys.readouterr().out
    for action in config.ACTIONS:
        assert action.template in out
    for name in config.SCREEN_ANCHORS:
        assert name in out


def test_resolution_guard_warns_without_crashing_when_capture_fails(
    monkeypatch, caplog
) -> None:
    """The guard must degrade to a warning, never abort startup - a device
    mock (or a real capture hiccup) must not take down the whole bot."""
    import tower_bot

    monkeypatch.setattr(tower_bot, "connect_device", lambda host, port: MagicMock())
    monkeypatch.setattr(tower_bot.TowerBot, "run_once", lambda self: False)

    exit_code = tower_bot.main(["--once"])

    assert exit_code == 0


def test_auto_navigate_does_not_start_a_run_past_the_cap(monkeypatch) -> None:
    """run_forever breaks at the TOP of the next iteration, but the scan that
    completed run N has already tapped RETRY on the way out - so `--max-runs 5`
    used to exit with run 6 live in the emulator."""
    import cv2
    import events
    import vision
    from tower_bot import TowerBot

    fixtures = Path(__file__).parent / "fixtures"
    bot = TowerBot(
        device=MagicMock(),
        templates=vision.TemplateCache(Path(__file__).parent.parent / "templates"),
        bus=events.EventBus(),
        auto_navigate=True,
    )
    bot._screen = cv2.imread(str(fixtures / "game_over.png"), cv2.IMREAD_COLOR)
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    bot.run_once()
    bot.run_once()  # settled on GAME_OVER

    navigated: list[bool] = []
    monkeypatch.setattr(
        bot.navigator, "maybe_navigate", lambda *a, **k: navigated.append(True)
    )
    bot.runs.completed = 2

    bot.run_once(max_runs=2)
    assert navigated == []  # cap reached: do not tap RETRY into run 3

    bot.run_once(max_runs=None)
    assert navigated == [True]  # control: uncapped, it still navigates


def test_tui_keeps_stdlib_logging_off_the_terminal() -> None:
    """rich's Live draws on stdout while logging writes to stderr, so INFO
    lines shred the panel. With BotError on the bus, nothing is lost."""
    import logging

    import tower_bot

    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        root.handlers.clear()
        tower_bot.configure_logging(tui=True)
        assert root.handlers
        assert all(isinstance(h, logging.NullHandler) for h in root.handlers)

        root.handlers.clear()
        tower_bot.configure_logging(tui=False)
        assert any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.NullHandler)
            for h in root.handlers
        )
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_affordability_defaults_to_digits() -> None:
    assert parse_args([]).affordability == "digits"


def test_affordability_can_be_forced_to_brightness() -> None:
    args = parse_args(["--affordability", "brightness"])
    assert args.affordability == "brightness"


def test_digits_without_an_atlas_degrades_to_brightness(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unlabelled atlas must not stop the bot - phase 2 behaviour is the
    floor, not an error."""
    with caplog.at_level(logging.WARNING):
        check = tower_bot.build_affordability("digits", atlas_root=tmp_path)
    assert isinstance(check, BrightnessAffordability)
    assert "atlas" in caplog.text.lower()


def test_digits_with_an_atlas_uses_digits(tmp_path: Path) -> None:
    for size_class in digits.SIZE_CLASSES:
        build_synthetic_atlas(tmp_path / size_class)
    check = tower_bot.build_affordability("digits", atlas_root=tmp_path)
    assert isinstance(check, DigitAffordability)


def test_a_partial_atlas_still_degrades(tmp_path: Path) -> None:
    """One size class built is not enough: the price is what gates a purchase,
    and a bot reading the wallet but never the price would gate on nothing."""
    build_synthetic_atlas(tmp_path / "wallet")
    check = tower_bot.build_affordability("digits", atlas_root=tmp_path)
    assert isinstance(check, BrightnessAffordability)


def test_brightness_is_used_even_when_an_atlas_exists(tmp_path: Path) -> None:
    for size_class in digits.SIZE_CLASSES:
        build_synthetic_atlas(tmp_path / size_class)
    check = tower_bot.build_affordability("brightness", atlas_root=tmp_path)
    assert isinstance(check, BrightnessAffordability)
