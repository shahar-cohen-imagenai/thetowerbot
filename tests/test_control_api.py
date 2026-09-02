"""The three routes that write - to memory, never to the database."""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import config
from control import Controls
from events import EventBus
from sinks.sse import SseSink
from sinks.state import BotState
from strategy import Strategy
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
    controls = Controls(strategy=replace(
        Strategy.from_config(), affordability="brightness"
    ))
    stop = threading.Event()
    app = create_app(
        state=BotState(), sse=SseSink(), bus=bus, db_path=None,
        unknown_dir=config.UNKNOWN_DIR, stop=stop,
        controls=controls, checks={"brightness": object(), "digits": None},
    )
    return TestClient(app), controls, seen, stop


def test_get_returns_the_whole_strategy(wired) -> None:
    client, _, _, _ = wired
    body = client.get("/api/control").json()
    assert body["paused"] is False
    assert body["strategy"]["affordability"] == "brightness"
    assert [row["name"] for row in body["strategy"]["actions"]] == [
        action.name for action in config.ACTIONS
    ]


def test_available_affordability_is_advertised_under_its_new_name(wired) -> None:
    client, _, _, _ = wired
    body = client.get("/api/control").json()
    # digits is None in the fixture's checks - no atlas on this machine - so
    # the browser must be told not to offer it.
    assert body["affordability_available"] == ["brightness"]


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
    assert controls.snapshot().strategy.interval == pytest.approx(
        Strategy.from_config().interval
    )
    assert not [event for event in seen if event.type == "ControlChanged"]


def test_patching_an_unavailable_affordability_is_refused(wired) -> None:
    """Silently downgrading would misrepresent what the bot is doing.

    build_affordability() degrades digits -> brightness on its own, which is
    right for a startup flag and wrong for a live setting: you would ask for
    digits, get brightness, and never be told.
    """
    client, controls, _, _ = wired
    response = client.patch("/api/control", json={"affordability": "digits"})
    assert response.status_code == 422
    assert "atlas" in response.json()["detail"]
    assert controls.snapshot().strategy.affordability == "brightness"


def test_patching_the_action_list_reorders_the_strategy(wired) -> None:
    client, controls, _, _ = wired
    rows = [row for row in reversed(client.get("/api/control").json()["strategy"]["actions"])]
    response = client.patch("/api/control", json={"actions": rows})
    assert response.status_code == 200
    assert [r.name for r in controls.snapshot().strategy.actions] == [
        row["name"] for row in rows
    ]

    # A client never has to guess what was accepted: the response carries the
    # full new state, not just the delta this patch mentioned.
    body = response.json()
    assert [row["name"] for row in body["strategy"]["actions"]] == [
        row["name"] for row in rows
    ]
    # affordability_available and affordability itself were never part of
    # this patch, yet the response still carries them.
    assert body["affordability_available"] == ["brightness"]
    assert body["strategy"]["affordability"] == "brightness"


def test_an_absolute_template_is_refused(wired) -> None:
    """The request body must not get to choose which file the bot reads.

    vision.TemplateCache.get() hands its path straight to cv2.imread, and an
    unresolvable one raises out of every scan pass forever - so this has to
    fail once, here, with a field name, rather than recurring as a BotError.
    """
    client, controls, seen, _ = wired
    before = controls.snapshot().strategy
    response = client.patch("/api/control", json={"actions": [
        {"name": "Damage", "template": "/etc/passwd"},
    ]})
    assert response.status_code == 422
    assert "template" in response.json()["detail"]
    assert controls.snapshot().strategy == before
    assert not [event for event in seen if event.type == "ControlChanged"]


def test_a_template_that_is_not_on_disk_is_refused(wired) -> None:
    # Inside TEMPLATE_DIR but absent: a typo must be a 422 naming the field,
    # not a bot that runs and fails on every pass.
    client, controls, _, _ = wired
    before = controls.snapshot().strategy
    response = client.patch("/api/control", json={"actions": [
        {"name": "Damage", "template": "nope.png"},
    ]})
    assert response.status_code == 422
    assert "nope.png" in response.json()["detail"]
    assert controls.snapshot().strategy == before


def test_a_real_template_still_patches(wired) -> None:
    # The other half of the check above: refusing everything would "fix" the
    # traversal by breaking the feature.
    client, controls, _, _ = wired
    real = config.ACTIONS[0].template
    response = client.patch("/api/control", json={"actions": [
        {"name": "Damage", "template": real, "threshold": 0.9},
    ]})
    assert response.status_code == 200
    rows = controls.snapshot().strategy.actions
    assert [(r.name, r.template, r.threshold) for r in rows] == [
        ("Damage", real, 0.9)
    ]


def test_an_invalid_patch_names_the_field(wired) -> None:
    client, _, _, _ = wired
    response = client.patch("/api/control", json={"interval": 0})
    assert response.status_code == 422
    assert "interval" in response.json()["detail"]


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
