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


def test_run_forever_stop_interrupts_a_long_interval_wait(monkeypatch) -> None:
    """stop() must end the between-scan wait immediately, not sleep it out.

    Regression: worker.join()'s timeout used to be sized off the interval
    directly, and the wait itself was a plain time.sleep() that stop() could
    not interrupt. With the browser dial at MAX_INTERVAL (3600s, a value
    Controls explicitly allows) that turned Ctrl+C into an hour-long hang.
    """
    import threading
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
    bot.controls.apply({"interval": 3600.0})  # the largest the dial allows

    finished = threading.Event()

    def _run() -> None:
        bot.run_forever()  # interval=None: reads the 3600s from controls
        finished.set()

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout=0.2)  # let it reach the between-scan wait
    assert not finished.is_set()  # sanity: it is actually waiting, not done

    bot.stop()

    assert finished.wait(timeout=1.0), "stop() did not interrupt the wait"
    worker.join(timeout=1.0)
    assert not worker.is_alive()


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
    from control import Controls
    from tower_bot import TowerBot

    fixtures = Path(__file__).parent / "fixtures"
    bot = TowerBot(
        device=MagicMock(),
        templates=vision.TemplateCache(Path(__file__).parent.parent / "templates"),
        bus=events.EventBus(),
        controls=Controls(auto_navigate=True),
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


def test_web_defaults_off() -> None:
    assert parse_args([]).web is False


def test_web_binds_loopback_by_default() -> None:
    """The dashboard serves session screenshots and has no auth."""
    args = parse_args(["--web"])
    assert args.web is True
    assert args.web_host == "127.0.0.1"


def test_the_store_is_on_by_default_and_can_be_turned_off() -> None:
    assert parse_args([]).store is True
    assert parse_args(["--no-store"]).store is False


def test_web_and_no_store_can_be_combined() -> None:
    """--web --no-store is a supported mode: a dashboard with no history."""
    args = parse_args(["--web", "--no-store"])
    assert args.web is True
    assert args.store is False


def test_the_database_path_can_be_overridden() -> None:
    assert parse_args(["--db", "/tmp/other.db"]).db == "/tmp/other.db"


def test_prepare_store_seeds_both_counters_and_prunes(tmp_path) -> None:
    """A restart must continue the sequence, not collide with it."""
    import time

    import db

    path = tmp_path / "bot.db"
    conn = db.connect(path)
    db.start_run(conn, 5, started_at=1.0)
    db.insert_event(conn, {
        "seq": 41, "run_id": 5, "ts": time.time(), "type": "Navigated",
        "screen": None, "action": None, "reason": None, "score": None,
        "price": None, "wallet": None, "detail": None,
    })
    db.insert_event(conn, {
        "seq": 42, "run_id": 5, "ts": time.time() - 90 * 86400, "type": "Navigated",
        "screen": None, "action": None, "reason": None, "score": None,
        "price": None, "wallet": None, "detail": None,
    })
    conn.close()

    seed_seq, last_run = tower_bot.prepare_store(path)

    assert (seed_seq, last_run) == (42, 5)
    with db.reader(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_a_seeded_bus_does_not_reissue_a_stored_seq() -> None:
    import events

    bus = events.EventBus(start_seq=42)

    assert bus.publish(events.Navigated(target="RETRY")).seq == 43


def test_prepare_store_closes_out_a_run_a_killed_process_left_live(tmp_path) -> None:
    """M4: no RunEnded ever fires for a killed (not stopped) bot, so the run
    keeps ended_at IS NULL - and the dashboard renders it as `live` forever.
    prepare_store is the one place that legitimately holds the writable
    connection outside the sink's own thread, so it is where this gets
    closed out."""
    import db

    path = tmp_path / "bot.db"
    conn = db.connect(path)
    db.start_run(conn, 1, started_at=100.0)  # never finished - the crash case
    db.finish_run(
        conn, 2, started_at=50.0, ended_at=90.0, wave=1, coins=1, tier=1,
        abandoned=False, scan_count=1, tap_count=1,
    )
    conn.close()

    tower_bot.prepare_store(path)

    with db.reader(path) as conn:
        runs = {r["id"]: r for r in db.list_runs(conn)}
    assert (runs[1]["ended_at"], runs[1]["abandoned"]) == (100.0, 1)
    # A run that already ended cleanly must not be touched.
    assert (runs[2]["ended_at"], runs[2]["abandoned"]) == (90.0, 0)


def test_prepare_store_logs_nothing_when_there_is_nothing_to_close(tmp_path, caplog) -> None:
    import db

    path = tmp_path / "bot.db"
    db.connect(path).close()

    with caplog.at_level(logging.INFO):
        tower_bot.prepare_store(path)

    assert "Closed" not in caplog.text


@pytest.mark.parametrize(
    "host, should_warn",
    [
        ("127.0.0.1", False),
        ("::1", False),
        ("0.0.0.0", True),
        ("192.168.1.50", True),
    ],
)
def test_warn_if_web_host_exposed(host, should_warn, caplog) -> None:
    """M5: the reflex for "reach it from my laptop" is --web-host 0.0.0.0,
    and the page it exposes serves live screenshots with no auth."""
    with caplog.at_level(logging.WARNING):
        tower_bot.warn_if_web_host_exposed(host)

    assert ("not loopback" in caplog.text) is should_warn


def test_once_with_web_does_not_advertise_a_dashboard_that_never_starts(
    monkeypatch, capsys
) -> None:
    """M1: --once wins over --web, so a bot that never starts the dashboard
    must not print its URL - the print used to run before the once/web
    branch and fire unconditionally."""
    import tower_bot

    monkeypatch.setattr(tower_bot, "connect_device", lambda host, port: MagicMock())
    monkeypatch.setattr(tower_bot.TowerBot, "run_once", lambda self: False)

    exit_code = tower_bot.main(["--once", "--web"])

    assert exit_code == 0
    assert "Dashboard on" not in capsys.readouterr().out


def test_web_alone_still_prints_the_dashboard_url(monkeypatch, capsys, tmp_path) -> None:
    """The other half of the guard above: --web without --once must still
    advertise the dashboard - printed before sink.start() (task 8, minor 6:
    under --tui that call hands the terminal to rich's Live, which would
    swallow a line printed any later), and `not args.once` must not silence
    this case too."""
    import tower_bot

    monkeypatch.setattr(tower_bot, "connect_device", lambda host, port: MagicMock())
    # No `interval` parameter: fails loudly (missing argument) if serve_web
    # ever goes back to forwarding one.
    monkeypatch.setattr(
        tower_bot.TowerBot,
        "run_forever",
        lambda self, max_runs=None: None,
    )
    _FakeServer.instances.clear()
    monkeypatch.setattr("uvicorn.Server", _FakeServer)

    exit_code = tower_bot.main(["--web", "--db", str(tmp_path / "bot.db")])

    assert exit_code == 0
    assert "Dashboard on http://127.0.0.1:8765" in capsys.readouterr().out


class _FakeServer:
    """Stands in for uvicorn.Server so the test never binds a real port."""

    instances: list["_FakeServer"] = []

    def __init__(self, config) -> None:
        self.config = config
        self.should_exit = False
        _FakeServer.instances.append(self)

    def run(self) -> None:
        return


def test_serve_web_runs_the_scan_loop_on_a_worker_not_the_main_thread(
    monkeypatch,
) -> None:
    """uvicorn owns the main thread; the loop must run on a worker thread,
    finish, and leave nothing behind once serve_web returns."""
    import threading
    from types import SimpleNamespace

    class FakeBot:
        def __init__(self) -> None:
            self.scanned = threading.Event()
            self.stopped = False
            self.ran_on: threading.Thread | None = None
            self.called_with: tuple[float | None, int | None] | None = None
            # serve_web reads this for the worker join timeout now that it no
            # longer takes an interval of its own.
            self.controls = SimpleNamespace(interval=0.01)

        def run_forever(self, interval: float | None = None, max_runs: int | None = None) -> None:
            self.called_with = (interval, max_runs)
            self.ran_on = threading.current_thread()
            self.scanned.set()

        def stop(self) -> None:
            self.stopped = True

    _FakeServer.instances.clear()
    monkeypatch.setattr("uvicorn.Server", _FakeServer)

    bot = FakeBot()
    tower_bot.serve_web(
        bot, object(), host="127.0.0.1", port=8123, max_runs=None
    )

    assert bot.scanned.wait(timeout=2)
    assert bot.stopped is True
    assert bot.ran_on is not None
    assert bot.ran_on is not threading.main_thread()
    assert not any(t.name == "scan-loop" for t in threading.enumerate())
    # Pins the contract harder than the old required-positional signature
    # did: catches a *reintroduced* interval, not just a missing argument.
    assert bot.called_with == (None, None)

    server = _FakeServer.instances[-1]
    assert (server.config.host, server.config.port) == ("127.0.0.1", 8123)


def test_serve_web_stops_the_server_once_the_scan_loop_ends(monkeypatch) -> None:
    """Finding: uvicorn.run() hides its Server, so nothing used to tell it to
    stop once --max-runs was reached - the process hung forever serving a
    dashboard attached to a dead bot. Owning the Server must fix that: the
    worker sets should_exit when run_forever returns, whether that is because
    the cap was reached or because the loop raised.

    Also covers finding 1's other half: an SSE stream's is_disconnected()
    never fires on its own, so the worker has to flip a `stop` flag too, not
    just should_exit, or a held-open dashboard tab keeps the server (and the
    scan loop behind it, as far as the user can tell) alive forever."""
    import threading as _threading
    from types import SimpleNamespace

    class FakeBot:
        def __init__(self) -> None:
            self.stopped = False
            self.called_with: tuple[float | None, int | None] | None = None
            # serve_web reads this for the worker join timeout now that it no
            # longer takes an interval of its own.
            self.controls = SimpleNamespace(interval=0.01)

        def run_forever(self, interval: float | None = None, max_runs: int | None = None) -> None:
            self.called_with = (interval, max_runs)
            return  # simulates --max-runs being reached immediately

        def stop(self) -> None:
            self.stopped = True

    _FakeServer.instances.clear()
    monkeypatch.setattr("uvicorn.Server", _FakeServer)

    bot = FakeBot()
    stop = _threading.Event()
    # Must return promptly rather than hang - that is the whole bug.
    tower_bot.serve_web(
        bot, object(), host="127.0.0.1", port=8123, max_runs=1,
        stop=stop,
    )

    assert _FakeServer.instances[-1].should_exit is True
    assert bot.stopped is True
    # Pins the contract harder than the old required-positional signature
    # did: catches a *reintroduced* interval, not just a missing argument.
    assert bot.called_with == (None, 1)
    assert stop.is_set() is True


def test_controls_start_from_the_command_line() -> None:
    """The flags you launched with must not be silently overridden.

    Controls' dataclass defaults are a fallback; main() seeds them from argv.
    """
    from control import Controls
    from tower_bot import parse_args

    args = parse_args(["--interval", "3.0", "--auto-navigate", "--affordability", "brightness"])
    controls = Controls(
        interval=args.interval, auto_navigate=args.auto_navigate, strategy=args.affordability
    )
    assert controls.snapshot()["interval"] == 3.0
    assert controls.snapshot()["auto_navigate"] is True
    assert controls.snapshot()["strategy"] == "brightness"
