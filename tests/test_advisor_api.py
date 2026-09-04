from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import events
from control import Controls
from sinks.sse import SseSink
from sinks.state import BotState
from strategy import ShoppingRule, Strategy, StrategyStore
from web.app import create_app
from web.advisor import DraftRequest, propose_draft


def payload(**row_changes: object) -> dict:
    now = time.time()
    return {
        "schema_version": 1,
        "source": {"name": "Effective Paths export", "version": "user-export-v1",
                   "account_name": "My tower", "exported_at": now,
                   "account_snapshot_at": now - 60},
        "missing_inputs": [],
        "recommendations": [{"id": "health-1", "path": "health", "system": "workshop",
                             "upgrade": "Health", "upgrade_id": "health", "current_value": 10,
                             "target_value": 20, "value_kind": "stat", "cost": 15,
                             "currency": "coins", "benefit": 1.2, **row_changes}],
    }


def setup() -> tuple[TestClient, Controls, Strategy]:
    strategy = Strategy.from_config()
    strategy = replace(strategy, shopping=replace(strategy.shopping, workshop=(
        ShoppingRule("Unlock Defense Upgrades", "DEFENSE"),
        ShoppingRule("Unlock Thorns", "DEFENSE"),
    ), coin_budget=500, coin_reserve=25, armed=True, allow_unlocks=True))
    controls = Controls(strategy)
    api = TestClient(create_app(state=BotState(), sse=SseSink(), bus=events.EventBus(),
                               db_path=None, controls=controls))
    return api, controls, strategy


def import_data(api: TestClient, data: dict) -> dict:
    response = api.post("/api/advisor/import", json={"profile": "default", "filename": "paths.json",
                                                   "content": json.dumps(data)})
    assert response.status_code == 200, response.text
    return response.json()


def stage(api: TestClient, snapshot: dict, strategy: Strategy) -> object:
    return api.post("/api/advisor/draft", json={"profile": "default",
                    "import_id": snapshot["import_id"], "recommendation_id": "health-1",
                    "draft": strategy.to_dict()})


def test_empty_advisor_is_available_without_running_bot() -> None:
    api, _, _ = setup()
    response = api.get("/api/advisor", params={"profile": "default"})
    assert response.status_code == 200
    assert response.json()["recommendations"] == []
    assert response.json()["import_id"] is None


def test_stage_appends_after_guides_without_saving_or_activating() -> None:
    api, controls, strategy = setup()
    imported = import_data(api, payload())
    response = stage(api, imported, strategy)
    assert response.status_code == 200, response.text
    result = Strategy.from_dict(response.json()["draft"])
    assert response.json()["added"] is True
    assert result.shopping.workshop[:-1] == strategy.shopping.workshop
    assert result.shopping.workshop[-1] == ShoppingRule("Health", "DEFENSE", target=20)
    assert replace(result.shopping, workshop=strategy.shopping.workshop) == strategy.shopping
    assert result.autopilot == strategy.autopilot
    assert controls.snapshot().strategy == strategy


def test_existing_alias_rule_keeps_its_disable_flag_cap_and_priority() -> None:
    api, _, strategy = setup()
    strategy = replace(strategy, shopping=replace(strategy.shopping,
                       workshop=(ShoppingRule("HP", "DEFENSE", enabled=False, target=12),)))
    response = stage(api, import_data(api, payload()), strategy)
    assert response.status_code == 200
    assert response.json()["added"] is False
    assert response.json()["draft"] == strategy.to_dict()


def test_level_targets_remain_advisory_and_do_not_become_stat_targets() -> None:
    api, _, strategy = setup()
    imported = import_data(api, payload(value_kind="level"))
    assert imported["recommendations"][0]["can_stage"] is False
    assert stage(api, imported, strategy).status_code == 409


def test_replaced_import_and_different_profile_draft_are_rejected() -> None:
    api, _, strategy = setup()
    old = import_data(api, payload())
    fresh = import_data(api, payload(target_value=30))
    assert stage(api, old, strategy).status_code == 409
    assert stage(api, fresh, replace(strategy, name="another")).status_code == 409


def test_invalid_import_preserves_previous_recommendations() -> None:
    api, _, _ = setup()
    before = import_data(api, payload())
    response = api.post("/api/advisor/import", json={"profile": "default", "filename": "paths.json",
                                                   "content": "{broken"})
    assert response.status_code == 422
    assert api.get("/api/advisor?profile=default").json()["import_id"] == before["import_id"]


def test_sample_template_is_explicitly_non_actionable() -> None:
    api, _, strategy = setup()
    sample = api.get("/api/advisor/template.json")
    assert sample.status_code == 200
    imported = import_data(api, sample.json())
    assert all(not row["can_stage"] for row in imported["recommendations"])
    assert stage(api, imported, strategy).status_code == 409


def test_csv_template_imports_as_non_actionable_example() -> None:
    api, _, _ = setup()
    sample = api.get("/api/advisor/template.csv")
    assert sample.status_code == 200
    response = api.post("/api/advisor/import", json={"profile": "default", "filename": "example.csv",
                                                   "content": sample.text})
    assert response.status_code == 200, response.text
    assert response.json()["recommendations"][0]["can_stage"] is False


def test_miscategorized_alias_is_not_duplicated() -> None:
    api, _, strategy = setup()
    strategy = replace(strategy, shopping=replace(strategy.shopping,
                       workshop=(ShoppingRule("HP", "ATTACK"),)))
    response = stage(api, import_data(api, payload()), strategy)
    assert response.status_code == 409
    assert "category" in response.json()["detail"]


def test_thorns_requires_enabled_prerequisite_or_observed_unlock() -> None:
    api, _, strategy = setup()
    imported = import_data(api, payload(upgrade="Thorn Damage", upgrade_id="thorns"))
    assert stage(api, imported, strategy).status_code == 200
    disabled = replace(strategy, shopping=replace(strategy.shopping, workshop=(
        ShoppingRule("Unlock Thorns", "DEFENSE", enabled=False),)))
    assert stage(api, imported, disabled).status_code == 409
    body = DraftRequest(profile="default", import_id=imported["import_id"],
                        recommendation_id="health-1", draft=disabled.to_dict())
    result = propose_draft(body, imported, [{"context": "workshop", "category": "DEFENSE",
                                           "upgrade_id": "thorns", "status": "available"}])
    assert result["added"] is True


def test_profile_paths_are_rejected() -> None:
    api, _, _ = setup()
    assert api.get("/api/advisor", params={"profile": "../default"}).status_code == 422
    assert api.post("/api/advisor/import", json={"profile": "../default", "filename": "paths.json",
                                               "content": json.dumps(payload())}).status_code == 422


def test_import_and_staging_leave_saved_profile_unchanged(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies")
    strategy = Strategy.from_config()
    store.save(strategy)
    before = store.path_for(strategy.name).read_bytes()
    api = TestClient(create_app(state=BotState(), sse=SseSink(), bus=events.EventBus(),
                               db_path=None, store=store))
    imported = import_data(api, payload())
    assert stage(api, imported, strategy).status_code == 200
    assert store.path_for(strategy.name).read_bytes() == before
    assert (tmp_path / "advisor.json").is_file()
    assert api.get("/api/advisor?profile=missing").status_code == 404
