"""The built dashboard is served, and the API still wins on /api."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from events import EventBus
from sinks.sse import SseSink
from sinks.state import BotState
from web.app import create_app

STATIC_DIR = Path(__file__).parent.parent / "web" / "static"


@pytest.fixture
def client() -> TestClient:
    bus = EventBus()
    return TestClient(
        create_app(
            state=BotState(),
            sse=SseSink(),
            bus=bus,
            db_path=None,
            unknown_dir=config.UNKNOWN_DIR,
            stop=threading.Event(),
        )
    )


def test_root_serves_the_built_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    # Next injects this on every exported page; the old hand-written file
    # never contained it, so this fails loudly if the build is not being served.
    assert "__NEXT_DATA__" in response.text or "/_next/" in response.text


def test_next_assets_are_served(client: TestClient) -> None:
    assets = list((STATIC_DIR / "_next").rglob("*.js"))
    assert assets, "no built JS - run `npm run build` in web/ui"
    url = "/_next/" + str(assets[0].relative_to(STATIC_DIR / "_next")).replace("\\", "/")
    assert client.get(url).status_code == 200


def test_api_routes_still_win_over_the_static_mount(client: TestClient) -> None:
    # The mount is registered at "/", so this is the regression that matters:
    # if it were registered before the API routes it would swallow them.
    response = client.get("/api/status")
    assert response.status_code == 200
    assert "screen" in response.json()
