"""The built dashboard is served, and the API still wins on /api."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from web import app as web_app
from events import EventBus
from sinks.sse import SseSink
from sinks.state import BotState
from tools.ui_manifest import ui_hash


def _client() -> TestClient:
    bus = EventBus()
    return TestClient(
        web_app.create_app(
            state=BotState(),
            sse=SseSink(),
            bus=bus,
            db_path=None,
            unknown_dir=config.UNKNOWN_DIR,
            shutdown=threading.Event(),
        )
    )


@pytest.fixture
def built_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    static = tmp_path / "static"
    (static / "_next").mkdir(parents=True)
    (static / "index.html").write_text(
        '<!doctype html><script src="/_next/app.js"></script>', encoding="utf-8"
    )
    (static / "_next" / "app.js").write_text("console.log('dashboard');", encoding="utf-8")
    monkeypatch.setattr(web_app, "STATIC_DIR", static)
    return _client()


@pytest.fixture
def unbuilt_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(web_app, "STATIC_DIR", tmp_path / "missing-static")
    return _client()


def test_root_serves_a_generated_index(built_client: TestClient) -> None:
    response = built_client.get("/")
    assert response.status_code == 200
    assert "/_next/app.js" in response.text


def test_generated_assets_are_served(built_client: TestClient) -> None:
    assert built_client.get("/_next/app.js").status_code == 200


def test_api_routes_still_win_over_the_static_mount(built_client: TestClient) -> None:
    # The mount is registered at "/", so this is the regression that matters:
    # if it were registered before the API routes it would swallow them.
    response = built_client.get("/api/status")
    assert response.status_code == 200
    assert "screen" in response.json()


def test_unbuilt_dashboard_tells_the_operator_to_use_the_launcher(
    unbuilt_client: TestClient,
) -> None:
    response = unbuilt_client.get("/")
    assert response.status_code == 200
    assert "Dashboard not built" in response.text
    assert "./run.sh" in response.text


UI_DIR = Path(__file__).parent.parent / "web" / "ui"


def test_ui_hash_changes_when_the_build_pipeline_changes(tmp_path: Path) -> None:
    """Build scripts set the embedded compatibility identity, so drift there
    must invalidate the static bundle just like a component edit does."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    pipeline = scripts / "publish.mjs"
    pipeline.write_text("export const version = 1;\n")
    before = ui_hash(tmp_path)

    pipeline.write_text("export const version = 2;\n")

    assert ui_hash(tmp_path) != before


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
