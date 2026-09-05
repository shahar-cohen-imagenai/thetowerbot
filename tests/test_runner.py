"""The thing the browser's Start button reaches.

Every test here injects a fake device factory: the runner's job is
lifecycle, and requiring a real emulator to test lifecycle would mean it
never got tested. The bot itself is faked too where the test is about
start/stop rather than about scanning.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

import config
import digits
import vision
from control import Controls
from events import EventBus
from runner import BotRunner, RunnerError
from shopping import ShoppingSession
from sinks.state import BotState
from strategy import ActionRule, Shopping, ShoppingRule, Strategy


class FakeBot:
    """Stands in for TowerBot: spins until stopped, and counts its runs.

    Keeps every construction kwarg. The runner builds each bot from the
    strategy that is active at Start - that is the whole mechanism behind
    "a strategy change applies on the next Start" - and a fake that quietly
    swallowed those kwargs would let the runner stop passing them with no
    test noticing.
    """

    def __init__(self, first_run_id: int = 1, **kwargs: Any) -> None:
        self.kwargs: dict[str, Any] = {"first_run_id": first_run_id, **kwargs}
        self.runs = type("R", (), {"next_id": first_run_id, "completed": 0})()
        self._stopping = threading.Event()
        self.scans = 0

    def run_forever(self, **kwargs) -> None:
        while not self._stopping.wait(0.001):
            self.scans += 1

    def stop(self, *_: object) -> None:
        self._stopping.set()


def a_strategy(**overrides) -> Strategy:
    base = dict(
        name="test",
        actions=(ActionRule(name="Damage", template="upgrade_damage.png"),),
    )
    return Strategy(**{**base, **overrides})


@pytest.fixture
def runner_parts():
    """A runner over fakes, plus the pieces a test needs to inspect."""
    made: list[FakeBot] = []
    devices: list[object] = []

    def device_factory():
        device = object()
        devices.append(device)
        return device

    def bot_factory(**kwargs):
        # Everything through, nothing dropped: see FakeBot's docstring.
        bot = FakeBot(**kwargs)
        made.append(bot)
        return bot

    bus = EventBus()
    seen: list = []
    bus.subscribe(type("R", (), {"offer": lambda self, e: seen.append(e) or True})())
    state = BotState()
    runner = BotRunner(
        bus=bus,
        controls=Controls(strategy=a_strategy()),
        state=state,
        templates=object(),
        device_factory=device_factory,
        checks={"brightness": object(), "digits": None},
        bot_factory=bot_factory,
    )
    return runner, made, devices, seen, state


def test_a_fresh_runner_is_not_running(runner_parts) -> None:
    runner, _, _, _, _ = runner_parts
    status = runner.status()
    assert status["running"] is False
    assert status["since"] is None
    assert status["error"] is None


def test_runner_identity_uses_the_connected_device_without_an_extra_lookup(runner_parts) -> None:
    runner, _, devices, _, _ = runner_parts
    device = type("Device", (), {"serial": "emulator-5554"})()
    runner._device_factory = lambda: devices.append(device) or device

    assert runner.identity() == {"serial": None, "game_version": None}
    runner.start()
    try:
        assert runner.identity() == {"serial": "emulator-5554", "game_version": None}
        assert devices == [device]
    finally:
        runner.stop()


def test_start_connects_a_device_and_spawns_a_bot(runner_parts) -> None:
    runner, made, devices, _, _ = runner_parts
    status = runner.start()
    try:
        assert status["running"] is True
        assert status["since"] is not None
        assert len(devices) == 1
        assert len(made) == 1
    finally:
        runner.stop()


def test_start_resets_a_shopping_session_left_mid_visit(runner_parts) -> None:
    """The restart half of the critical fix: stop the bot mid-visit, and the
    ShoppingSession's `_step` is still non-IDLE. Without this, the NEXT
    bot's very first run_once() would see `shopping.active` True and call
    advance() directly - skipping begin() entirely, and with it the enabled
    check, the cadence, the run cap and the MAIN_MENU precondition - while
    resuming against a stale `_categories` list from the previous policy.
    reset() at Start closes that regardless of what the live policy says now.
    """
    runner, made, devices, seen, state = runner_parts
    shopping = ShoppingSession(
        templates=vision.TemplateCache(config.TEMPLATE_DIR),
        bus=EventBus(),
        reader=digits.NumberReader(),
    )
    policy = Shopping(
        enabled=True, armed=False,
        workshop=(ShoppingRule(name="Damage", category="ATTACK"),),
    )
    shopping.begin(policy, run_count=1)
    assert shopping.active, "the fixture must actually be mid-visit to test anything"

    runner._shopping = shopping
    runner.start()
    try:
        assert shopping.active is False
    finally:
        runner.stop()


def test_stop_ends_the_worker(runner_parts) -> None:
    runner, made, _, _, _ = runner_parts
    runner.start()
    status = runner.stop()
    assert status["running"] is False
    # The thread is genuinely gone, not merely flagged as stopped.
    assert not runner._thread.is_alive()


def test_starting_twice_is_refused_rather_than_silently_ignored(runner_parts) -> None:
    """The browser asked for something that did not happen. Saying so beats
    a 200 that means nothing."""
    runner, made, _, _, _ = runner_parts
    runner.start()
    try:
        with pytest.raises(RunnerError) as caught:
            runner.start()
        assert caught.value.status_code == 409
        assert len(made) == 1
    finally:
        runner.stop()


def test_stopping_a_stopped_runner_is_harmless(runner_parts) -> None:
    """Idempotent on purpose: a double-click on Stop, or a Stop racing the
    bot hitting max_runs, must not raise.

    Starts first, deliberately. Two stops on a runner that was never started
    both take the `bot is None` early return and prove nothing about the
    real double-stop path, which is the interesting one: the second call
    calls stop() on an already-stopped bot and joins a thread that is
    already dead.
    """
    runner, _, _, _, _ = runner_parts
    runner.start()
    assert runner.stop()["running"] is False
    assert runner.stop()["running"] is False


def test_a_stop_the_loop_ignores_is_reported_as_still_running(runner_parts) -> None:
    """The contradiction this exists to prevent: stop() answering "stopped"
    after a join it just watched time out. status() derives running from
    thread liveness, so a hardcoded False here meant POST /api/bot/stop said
    stopped, the next /api/status poll said running, the next Start was
    refused with 409 - and the bot was still tapping the game throughout.
    Entirely reachable: a scan pass stuck on an ADB read outlasts the
    timeout, and a stalled ADB read is exactly when someone presses Stop.
    """
    runner, _, _, _, _ = runner_parts

    class DeafBot(FakeBot):
        def stop(self, *_: object) -> None:
            pass  # ignores the request, exactly like a wedged scan pass

    runner._bot_factory = lambda **kwargs: DeafBot(**kwargs)
    runner.start()
    try:
        status = runner.stop(timeout=0.05)
        assert status["running"] is True
        assert status["since"] is not None
        # The two answers agree, which is the entire point.
        assert runner.status()["running"] is True
    finally:
        # Release it for real, or the daemon thread spins for the session.
        runner._bot._stopping.set()
        runner._thread.join(timeout=5)


def test_each_bot_is_built_from_the_strategy_active_at_start(runner_parts) -> None:
    """"Applies on the next Start" is not a doc claim, it is these two
    kwargs: the runner reads controls at start() and hands the values to the
    new bot, which holds them for its whole life. Nothing else covers the
    mechanism, and a runner that quietly stopped passing them would look
    fine everywhere else.
    """
    runner, made, _, _, _ = runner_parts
    runner._controls.replace(
        a_strategy(screen_confirmations=4, navigation_cooldown=9.0)
    )
    runner.start()
    try:
        assert made[0].kwargs["screen_confirmations"] == 4
        assert made[0].kwargs["navigation_cooldown"] == 9.0
    finally:
        runner.stop()


def test_a_crashing_loop_is_published_and_leaves_the_runner_stopped(
    runner_parts,
) -> None:
    """A bot that raises out of run_forever() must end like any other: the
    error on the feed and in status(), the thread gone, and the runner ready
    to start another. Silence here would leave the dashboard showing a
    running bot that is not there.
    """
    runner, _, _, seen, _ = runner_parts

    def explodes(**kwargs):
        bot = FakeBot(**kwargs)

        def boom(**_: object) -> None:
            raise RuntimeError("matchTemplate blew up")

        bot.run_forever = boom
        return bot

    runner._bot_factory = explodes
    runner.start()
    for _ in range(200):
        if not runner.status()["running"]:
            break
        time.sleep(0.01)

    assert runner.status()["running"] is False
    assert runner.status()["error"] == "matchTemplate blew up"
    assert any(
        e.type == "BotError" and "matchTemplate" in e.message for e in seen
    )


def test_run_ids_carry_across_a_restart(runner_parts) -> None:
    """The failure this exists to stop: session 2's run 2 overwriting
    session 1's run 2 in the runs table."""
    runner, made, _, _, _ = runner_parts
    runner.start()
    made[0].runs.next_id = 12  # the first bot completed some runs
    runner.stop()

    runner.start()
    try:
        assert made[1].runs.next_id == 12
    finally:
        runner.stop()


def test_start_resets_bot_state_but_not_the_bus(runner_parts) -> None:
    import events as ev

    runner, _, _, seen, state = runner_parts
    state.apply(ev.ScanCompleted(seq=1, ts=time.time(), screen="IN_RUN", duration_ms=1.0))
    before_seq = runner._bus.publish(ev.Navigated(target="BATTLE")).seq

    runner.start()
    try:
        assert state.snapshot()["scans"] == 0
        # The bus is deliberately NOT reset: seq must keep climbing so a
        # reconnecting browser's Last-Event-ID stays meaningful across a
        # restart, which is exactly when you are watching.
        assert runner._bus.publish(ev.Navigated(target="BATTLE")).seq > before_seq
    finally:
        runner.stop()


def test_a_dead_emulator_is_reported_not_raised_out_of_the_process(runner_parts) -> None:
    """Today a bad device returns exit 1 from main() before anything serves.
    In idle mode it must not take the dashboard down with it."""
    runner, _, _, seen, _ = runner_parts

    def broken():
        from device import EmulatorError

        raise EmulatorError("no emulator at 127.0.0.1:5555")

    runner._device_factory = broken
    with pytest.raises(RunnerError) as caught:
        runner.start()
    assert caught.value.status_code == 503
    assert "no emulator" in str(caught.value)

    assert runner.status()["running"] is False
    assert runner.status()["error"] is not None
    # And it lands in the feed and on the errors page like every other failure.
    assert any(e.type == "BotError" for e in seen)


def test_the_error_clears_on_a_successful_start(runner_parts) -> None:
    runner, _, _, _, _ = runner_parts

    def broken():
        from device import EmulatorError

        raise EmulatorError("nope")

    original = runner._device_factory
    runner._device_factory = broken
    with pytest.raises(RunnerError):
        runner.start()
    assert runner.status()["error"] is not None

    runner._device_factory = original
    runner.start()
    try:
        assert runner.status()["error"] is None
    finally:
        runner.stop()


def test_a_bot_that_ends_on_its_own_leaves_the_runner_stopped(runner_parts) -> None:
    """max_runs reached. The server keeps serving; only the bot ended."""
    runner, _, _, _, _ = runner_parts

    def ends_immediately(*, device, first_run_id, **kwargs):
        bot = FakeBot(first_run_id=first_run_id)
        bot.run_forever = lambda **k: None
        return bot

    runner._bot_factory = ends_immediately
    runner.start()
    for _ in range(200):
        if not runner.status()["running"]:
            break
        time.sleep(0.01)
    assert runner.status()["running"] is False


def test_concurrent_starts_spawn_exactly_one_bot(runner_parts) -> None:
    runner, made, _, _, _ = runner_parts
    refused: list[RunnerError] = []

    def racer() -> None:
        try:
            runner.start()
        except RunnerError as exc:
            refused.append(exc)

    threads = [threading.Thread(target=racer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert len(made) == 1
        assert len(refused) == 7
    finally:
        runner.stop()


def test_account_state_survives_runner_restart(runner_parts: tuple) -> None:
    runner, made, _, _, _ = runner_parts
    shared = runner.account_state
    runner.start()
    assert made[-1].kwargs['account_state'] is shared
    runner.stop()
    runner.start()
    assert made[-1].kwargs['account_state'] is shared
    runner.stop()
