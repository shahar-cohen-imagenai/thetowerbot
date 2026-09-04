"""Guide presets combine safe battle rules with between-run Workshop rows."""

from __future__ import annotations

import json

import pytest

from guide_presets import preset_payload
from policy import PolicyError, preset_rules
from strategy import Shopping, ShoppingRule
from upgrades import resolve


def _workshop_ids(payload: dict[str, object]) -> list[str]:
    rows = payload["workshop"]
    assert isinstance(rows, list)
    ids: list[str] = []
    for row in rows:
        assert isinstance(row, dict)
        found = resolve(row["name"], row["category"])
        assert found is not None
        ids.append(found.id)
    return ids


def test_shopping_rule_owns_its_json_shape() -> None:
    rule = ShoppingRule("Thorns", "DEFENSE", target=11)
    assert rule.to_dict() == {
        "name": "Thorns",
        "category": "DEFENSE",
        "enabled": True,
        "target": 11,
    }


def test_manual_guide_is_an_empty_json_ready_plan() -> None:
    payload = preset_payload("manual")
    assert set(payload) == {
        "name",
        "rules",
        "workshop",
        "description",
        "notes",
        "sources",
    }
    assert payload["name"] == "manual"
    assert payload["rules"] == []
    assert payload["workshop"] == []
    assert json.loads(json.dumps(payload)) == payload


def test_turtle_guide_unlocks_defense_and_thorns_before_buying_stats() -> None:
    payload = preset_payload("turtle")
    workshop_ids = _workshop_ids(payload)
    assert workshop_ids[:2] == ["unlock_defense_upgrades", "unlock_thorns"]
    assert workshop_ids.index("unlock_thorns") < workshop_ids.index("thorns")
    assert workshop_ids.index("unlock_defense_upgrades") < workshop_ids.index(
        "defense_absolute"
    )
    assert workshop_ids.index("unlock_cash_bonuses") < workshop_ids.index(
        "cash_per_wave"
    )
    assert workshop_ids.index("unlock_coin_bonuses") < workshop_ids.index(
        "coins_per_wave"
    )
    thorns = payload["workshop"][workshop_ids.index("thorns")]
    assert thorns["target"] == 11
    assert payload["rules"] == [rule.to_dict() for rule in preset_rules("turtle")]
    assert any("50" in note and "target" in note for note in payload["notes"])
    assert {source["url"] for source in payload["sources"]} >= {
        "https://www.tower-hub.com/wiki/guide/beginner-guide",
        "https://the-tower.notion.site/Turtle-Strategy-early-game-1bc91383b93f809c81d0e2825cfeac07",
    }


def test_health_guide_contains_required_unlock_chains_without_late_game_sinks() -> None:
    payload = preset_payload("health")
    workshop_ids = _workshop_ids(payload)
    required_unlocks = [
        "unlock_range_upgrades",
        "unlock_multishot",
        "unlock_rapid_fire",
        "unlock_defense_upgrades",
        "unlock_thorns",
        "unlock_lifesteal",
        "unlock_knockback",
        "unlock_orbs",
        "unlock_cash_bonuses",
        "unlock_coin_bonuses",
        "unlock_free_upgrades",
    ]
    assert all(unlock_id in workshop_ids for unlock_id in required_unlocks)
    assert "wall_health" not in workshop_ids
    assert "wall_rebuild" not in workshop_ids
    assert "rend_armor_chance" not in workshop_ids
    assert "rend_armor_mult" not in workshop_ids
    assert workshop_ids.index("unlock_orbs") < workshop_ids.index("orbs")
    assert workshop_ids.index("unlock_free_upgrades") < workshop_ids.index(
        "free_defense_upgrade"
    )
    assert {source["url"] for source in payload["sources"]} >= {
        "https://www.tower-hub.com/wiki/guide/health-build",
        "https://the-tower.notion.site/Blender-Strategy-early-game-1bc91383b93f80e4b384c148c67b66f8",
    }


def test_health_workshop_order_tracks_the_guide_priorities() -> None:
    workshop_ids = _workshop_ids(preset_payload("health"))
    priority = [
        "coins_per_kill_bonus",
        "health",
        "defense_percent",
        "knockback_chance",
        "orbs",
        "thorns",
        "attack_speed",
        "free_utility_upgrade",
        "free_defense_upgrade",
        "free_attack_upgrade",
        "lifesteal",
    ]
    positions = [workshop_ids.index(upgrade_id) for upgrade_id in priority]
    assert positions == sorted(positions)
    assert "damage_per_meter" not in workshop_ids
    assert "multishot_chance" not in workshop_ids
    assert "rapid_fire_chance" not in workshop_ids


@pytest.mark.parametrize("name", ["turtle", "health"])
def test_guide_workshop_rows_use_canonical_names_and_parse_as_shopping(
    name: str,
) -> None:
    payload = preset_payload(name)
    restored = Shopping.from_dict({"workshop": payload["workshop"]})
    assert len(restored.workshop) == len(payload["workshop"])
    assert len(_workshop_ids(payload)) == len(set(_workshop_ids(payload)))
    assert all("price" not in row for row in payload["workshop"])


def test_presets_do_not_carry_any_spending_or_activation_authority() -> None:
    payload = preset_payload("health")
    forbidden = {
        "enabled",
        "armed",
        "allow_unlocks",
        "coin_budget",
        "coin_reserve",
        "visit_every_n_runs",
        "max_taps_per_visit",
        "cards",
    }
    assert forbidden.isdisjoint(payload)


def test_preset_payload_returns_fresh_mutable_json_data() -> None:
    changed = preset_payload("turtle")
    changed["workshop"].clear()
    assert preset_payload("turtle")["workshop"]


def test_unknown_guide_name_uses_the_existing_policy_error_contract() -> None:
    with pytest.raises(PolicyError) as caught:
        preset_payload("glass-cannon")
    assert caught.value.field == "preset"
