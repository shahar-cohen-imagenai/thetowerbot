"""The built dashboard is served, and the API still wins on /api."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from events import EventBus
from sinks.sse import SseSink
from sinks.state import BotState
from tools.ui_manifest import ui_hash
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
            shutdown=threading.Event(),
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


UI_DIR = Path(__file__).parent.parent / "web" / "ui"


def test_committed_build_matches_the_ui_sources() -> None:
    """The committed build is not stale.

    Committing build output buys `git clone && --web` with no node, and costs
    exactly one new failure mode: source edited, build not rerun, stale bundle
    shipped. This is that failure mode's test.
    """
    manifest_path = STATIC_DIR / ".build-manifest.json"
    assert manifest_path.is_file(), "no build manifest - run `npm run build` in web/ui"
    recorded = json.loads(manifest_path.read_text(encoding="utf-8"))["hash"]
    assert recorded == ui_hash(UI_DIR), (
        "web/static/ is stale against web/ui/ - run `npm run build` in web/ui "
        "and commit the result"
    )


EVENT_REDUCER_PATH = UI_DIR / "lib" / "eventReducer.ts"


def test_feed_limit_matches_the_sse_ring_size() -> None:
    """web/ui/lib/eventReducer.ts's FEED_LIMIT and config.SSE_RING_SIZE are two
    hardcoded constants in two languages with nothing tying them together.
    Change either alone and nothing else fails - the drift would only show up
    as a subtly wrong feed length in a browser. This is that invariant's test.
    """
    source = EVENT_REDUCER_PATH.read_text(encoding="utf-8")
    match = re.search(r"FEED_LIMIT\s*=\s*(\d+)", source)
    assert match, f"could not find FEED_LIMIT in {EVENT_REDUCER_PATH}"
    feed_limit = int(match.group(1))
    assert feed_limit == config.SSE_RING_SIZE, (
        f"FEED_LIMIT in {EVENT_REDUCER_PATH} ({feed_limit}) does not match "
        f"config.SSE_RING_SIZE in config.py ({config.SSE_RING_SIZE}) - "
        "the browser's feed cap must match the server's SSE ring size"
    )


NEXT_CONFIG_PATH = UI_DIR / "next.config.ts"


def test_dev_proxy_port_matches_web_port() -> None:
    """web/ui/next.config.ts hardcodes the bot's dev-server port and
    config.WEB_PORT is two hardcoded constants in two languages with nothing
    tying them together. Change either alone and nothing else fails - `npm
    run dev` just proxies `/api/*` into a void with no other symptom. This is
    that invariant's test.
    """
    source = NEXT_CONFIG_PATH.read_text(encoding="utf-8")
    match = re.search(r"http://127\.0\.0\.1:(\d+)", source)
    assert match, f"could not find the proxied port in {NEXT_CONFIG_PATH}"
    proxy_port = int(match.group(1))
    assert proxy_port == config.WEB_PORT, (
        f"the dev-proxy port in {NEXT_CONFIG_PATH} ({proxy_port}) does not match "
        f"config.WEB_PORT in config.py ({config.WEB_PORT}) - "
        "a mismatch makes `npm run dev` proxy into a void with no other symptom"
    )


def test_the_tap_jitter_input_cap_matches_the_server_ceiling() -> None:
    """Same class of invariant as the dev-proxy port, and worse in one way.

    The editor's other bounds (interval, the cooldowns) are literals on both
    sides, so they drift only if someone edits one of them. MAX_TAP_JITTER_PX
    is DERIVED from config.PRICE_REGION, so re-measuring that region for a
    new screen resolution silently moves the server's ceiling while leaving
    the input's max where it was. The symptom is a dashboard that offers a
    radius the PATCH then rejects with a 422 - or worse, in the other
    direction, quietly caps the operator below what is actually safe.
    """
    from strategy import MAX_TAP_JITTER_PX

    editor = (
        Path(__file__).parent.parent / "web" / "ui" / "components" / "StrategyEditor.tsx"
    ).read_text(encoding="utf-8")
    match = re.search(r'label="Tap jitter \(px\)".*?max=\{([\d.]+)\}', editor, re.S)
    assert match, "could not find the Tap jitter field's max in StrategyEditor.tsx"

    assert float(match.group(1)) == MAX_TAP_JITTER_PX, (
        f"the Tap jitter input caps at {match.group(1)} but strategy."
        f"MAX_TAP_JITTER_PX is {MAX_TAP_JITTER_PX} - the dashboard would offer "
        "a radius the server refuses, or cap below what is safe"
    )
