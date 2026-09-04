from __future__ import annotations

import time
from dataclasses import replace

from fastapi.testclient import TestClient

import events
from sinks.sse import SseSink
from sinks.state import BotState
from web.app import create_app


def client() -> TestClient:
    return TestClient(create_app(state=BotState(), sse=SseSink(), bus=events.EventBus(), db_path=None))


def test_catalog_and_presets_are_available_without_a_running_bot() -> None:
    api = client()
    response = api.get("/api/upgrades")
    assert response.status_code == 200
    rows = response.json()
    assert any(r["id"] == "thorns" and r["category"] == "DEFENSE" for r in rows)
    assert any(r["id"] == "cash_bonus" for r in rows)
    assert {p["name"] for p in api.get("/api/autopilot/presets").json()} == {"manual", "turtle", "health"}


def test_guide_presets_include_serializable_workshop_and_battle_plans() -> None:
    from strategy import Strategy

    presets = client().get("/api/autopilot/presets").json()
    turtle = next(p for p in presets if p["name"] == "turtle")
    catalog = client().get("/api/upgrades").json()
    thorn_unlock = next(row for row in catalog if row.get("unlock") and "thorns" in row.get("unlocks", []))
    assert any(row["name"] == thorn_unlock["name"] for row in turtle["workshop"])
    assert any(row["upgrade_id"] == "thorns" for row in turtle["rules"])
    assert turtle["description"] and turtle["notes"] and turtle["sources"]
    for preset in presets:
        data = Strategy.from_config().to_dict()
        data["autopilot"].update(preset=preset["name"], rules=preset["rules"])
        data["shopping"]["workshop"] = preset["workshop"]
        saved = Strategy.from_dict(data)
        assert saved.to_dict()["shopping"]["workshop"] == preset["workshop"]
        assert not saved.shopping.armed
        assert saved.shopping.coin_budget == 0


def test_idle_state_is_unknown_and_manual_actions_are_rejected() -> None:
    api = client()
    response = api.get("/api/autopilot")
    assert response.status_code == 200
    body = response.json()
    assert body["observations"] == []
    assert body["can_control"] is False
    assert api.post("/api/autopilot/command", json={"action":"buy", "upgrade_id":"health"}).status_code == 409


def test_tier_comparison_ignores_partial_runs_and_requires_repeated_evidence() -> None:
    from progression import compare_tiers
    runs = [dict(tier=1, started_at=0, ended_at=3600, coins=100, wave=100, abandoned=0)] * 3
    runs += [dict(tier=2, started_at=0, ended_at=None, coins=10000, wave=1000, abandoned=0)]
    runs += [dict(tier=3, started_at=0, ended_at=3600, coins=300, wave=50, abandoned=0)]
    result = compare_tiers(runs)
    assert result["recommended_tier"] == 1
    assert {r["tier"] for r in result["tiers"]} == {1,3}
    assert result["tiers"][0]["coins_per_hour"] == 100


def test_manual_api_requires_fresh_buy_evidence_but_scan_can_bootstrap() -> None:
    from control import Controls
    from strategy import Strategy
    from tests.test_autopilot import parts

    bot, _, _, observation, _ = parts()
    queued: list[dict] = []

    class Running:
        autopilot_state = bot.state

        def status(self) -> dict:
            return {"running": True}

        def request_autopilot(self, command: dict) -> None:
            queued.append(command)

    state = BotState()
    state.apply(events.ScreenChanged(prev="UNKNOWN", curr="IN_RUN", confidence=1, scores={}))
    controls = Controls(Strategy.from_config())
    api = TestClient(create_app(state=state, sse=SseSink(), bus=events.EventBus(),
                               db_path=None, controls=controls, runner=Running()))
    assert api.post("/api/autopilot/command", json={"action":"scan"}).status_code == 200
    purchase = {"action":"buy", "upgrade_id":"damage"}
    assert api.post("/api/autopilot/command", json=purchase).status_code == 409
    now = time.time()
    bot.state.observe(replace(observation, observed_at=now,
                             rows=tuple(replace(row, observed_at=now) for row in observation.rows)))
    assert api.post("/api/autopilot/command", json=purchase).status_code == 200
    assert queued == [{"action":"scan"}, purchase]
    controls.apply({"paused":True})
    assert api.post("/api/autopilot/command", json=purchase).status_code == 409
    assert len(queued) == 2
