"""The three routes that write - to memory, never to the database."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

import config
from control import Controls
from events import EventBus
from sinks.sse import SseSink
from sinks.state import BotState
from web.app import create_app


@pytest.fixture
def wired() -> tuple[TestClient, Controls, list, threading.Event]:
    seen: list = []

    class Recorder:
        def offer(self, event) -> bool:
            seen.append(event)
            return True

    bus = EventBus()
    bus.subscribe(Recorder())
    controls = Controls(paused=False, interval=2.0, strategy="brightness")
    stop = threading.Event()
    app = create_app(
        state=BotState(), sse=SseSink(), bus=bus, db_path=None,
        unknown_dir=config.UNKNOWN_DIR, stop=stop,
        controls=controls, checks={"brightness": object(), "digits": None},
    )
    return TestClient(app), controls, seen, stop


def test_get_returns_the_current_settings_and_the_action_names(wired) -> None:
    client, _, _, _ = wired
    body = client.get("/api/control").json()
    assert body["paused"] is False
    assert body["strategy"] == "brightness"
    assert body["actions"] == [action.name for action in config.ACTIONS]


def test_patch_applies_and_echoes_the_full_state(wired) -> None:
    client, controls, _, _ = wired
    body = client.patch("/api/control", json={"paused": True}).json()
    assert body["paused"] is True
    # The full state, not just the delta: a client must never have to guess
    # what was accepted.
    assert "interval" in body and "enabled_actions" in body
    assert controls.snapshot()["paused"] is True


def test_patch_publishes_a_control_changed_event(wired) -> None:
    client, _, seen, _ = wired
    client.patch("/api/control", json={"interval": 5.0})
    published = [event for event in seen if event.type == "ControlChanged"]
    assert published and published[0].changed == {"interval": 5.0}


def test_a_no_op_patch_publishes_nothing(wired) -> None:
    client, _, seen, _ = wired
    client.patch("/api/control", json={"paused": False})
    assert not [event for event in seen if event.type == "ControlChanged"]


def test_an_invalid_patch_is_422_and_changes_nothing(wired) -> None:
    client, controls, seen, _ = wired
    response = client.patch("/api/control", json={"interval": -1})
    assert response.status_code == 422
    assert controls.snapshot()["interval"] == 2.0
    assert not [event for event in seen if event.type == "ControlChanged"]


def test_switching_to_an_unavailable_strategy_is_refused(wired) -> None:
    """Silently downgrading would misrepresent what the bot is doing.

    build_affordability() degrades digits -> brightness on its own, which is
    right for a startup flag and wrong for a live setting: you would ask for
    digits, get brightness, and never be told.
    """
    client, controls, _, _ = wired
    response = client.patch("/api/control", json={"strategy": "digits"})
    assert response.status_code == 422
    assert "atlas" in response.json()["detail"].lower()
    assert controls.snapshot()["strategy"] == "brightness"


def test_stop_sets_the_shutdown_flag(wired) -> None:
    client, _, _, stop = wired
    assert client.post("/api/control/stop").status_code == 200
    assert stop.is_set()


def test_control_routes_are_absent_when_no_controls_were_wired(wired) -> None:
    """--once and the tests that predate this build an app with no controls."""
    app = create_app(
        state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None,
        unknown_dir=config.UNKNOWN_DIR, stop=threading.Event(),
    )
    assert TestClient(app).get("/api/control").status_code == 404
