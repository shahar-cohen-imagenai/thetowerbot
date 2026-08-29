from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

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
