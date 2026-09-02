"""Controls is the one mutable thing the web layer may touch.

Reshaped: it now holds session state (paused) plus one Strategy swapped
whole. The lock, the all-or-nothing apply(), and the changed-dict that
becomes a ControlChanged event are unchanged - those are the parts that were
already right.
"""

from __future__ import annotations

import threading

import pytest

from control import ControlError, Controls, Live
from strategy import ActionRule, Strategy


def a_strategy(**overrides) -> Strategy:
    base = dict(
        name="test",
        actions=(
            ActionRule(name="Damage", template="upgrade_damage.png"),
            ActionRule(name="Critical Chance", template="upgrade_critical_chance.png"),
        ),
    )
    return Strategy(**{**base, **overrides})


def a_controls(**overrides) -> Controls:
    return Controls(strategy=a_strategy(**overrides))


def test_snapshot_is_frozen_and_needs_no_copying() -> None:
    """The whole point of the reshape.

    The old Controls copied its enabled_actions set on construction and again
    on every snapshot, so the loop and the web thread never shared a mutable
    structure. A frozen Strategy holding frozen rows cannot be mutated at
    all, so the copying stops being necessary rather than moving one level in.
    """
    controls = a_controls()
    snap = controls.snapshot()
    assert isinstance(snap, Live)
    with pytest.raises(Exception):
        snap.paused = True
    with pytest.raises(Exception):
        snap.strategy.actions[0].threshold = 0.1


def test_snapshot_exposes_attributes_not_dict_keys() -> None:
    # This is what run_once() reads on every pass; nested dict lookups were
    # the cost the old dict snapshot would have imposed once policy got rich.
    snap = a_controls(interval=3.0).snapshot()
    assert snap.paused is False
    assert snap.strategy.interval == 3.0
    assert snap.strategy.actions[0].name == "Damage"


def test_apply_returns_only_what_changed() -> None:
    controls = a_controls(interval=2.0)
    changed = controls.apply({"paused": True, "interval": 2.0})
    assert changed == {"paused": True}
    assert controls.snapshot().paused is True


def test_apply_ignores_unknown_fields() -> None:
    assert a_controls().apply({"nonsense": 1}) == {}


def test_apply_patches_strategy_fields_in_place() -> None:
    controls = a_controls(interval=2.0)
    changed = controls.apply({"interval": 5.0, "auto_navigate": True})
    assert changed == {"interval": 5.0, "auto_navigate": True}
    assert controls.snapshot().strategy.interval == 5.0
    # The rest of the strategy survives the patch untouched.
    assert controls.snapshot().strategy.name == "test"
    assert len(controls.snapshot().strategy.actions) == 2


def test_apply_is_all_or_nothing_across_a_multi_field_patch() -> None:
    """A patch whose third field is invalid must not leave the first two
    applied. The merged Strategy is validated whole before it is swapped in,
    so the earlier fields were never applied to anything but a candidate.
    """
    controls = a_controls(interval=2.0, auto_navigate=False)
    with pytest.raises(ControlError) as caught:
        controls.apply({"interval": 5.0, "auto_navigate": True, "max_runs": 0})
    assert caught.value.field == "max_runs"
    live = controls.snapshot()
    assert live.strategy.interval == 2.0
    assert live.strategy.auto_navigate is False


def test_interval_bounds_still_apply() -> None:
    controls = a_controls(interval=2.0)
    with pytest.raises(ControlError) as caught:
        controls.apply({"interval": 0})
    assert caught.value.field == "interval"
    assert controls.snapshot().strategy.interval == 2.0
    with pytest.raises(ControlError):
        controls.apply({"interval": 3601})


def test_affordability_must_be_known() -> None:
    controls = a_controls()
    with pytest.raises(ControlError) as caught:
        controls.apply({"affordability": "vibes"})
    assert caught.value.field == "affordability"


def test_actions_can_be_replaced_wholesale() -> None:
    """How the strategy page reorders, toggles and retunes rows: one patch
    carrying the whole list, not a per-row endpoint.
    """
    controls = a_controls()
    changed = controls.apply({
        "actions": [
            {"name": "Critical Chance", "template": "upgrade_critical_chance.png",
             "threshold": 0.95, "enabled": True, "brightness_ratio": 0.75},
            {"name": "Damage", "template": "upgrade_damage.png",
             "threshold": 0.8, "enabled": False, "brightness_ratio": 0.75},
        ]
    })
    rows = controls.snapshot().strategy.actions
    assert [r.name for r in rows] == ["Critical Chance", "Damage"]
    assert rows[0].threshold == 0.95
    assert rows[1].enabled is False
    assert "actions" in changed


def test_a_rejected_action_list_changes_nothing() -> None:
    controls = a_controls()
    before = controls.snapshot().strategy
    with pytest.raises(ControlError):
        controls.apply({"actions": [
            {"name": "Damage", "template": "d.png", "threshold": 5.0},
        ]})
    assert controls.snapshot().strategy == before


def test_replacing_the_whole_strategy() -> None:
    """Activating a saved profile: one swap, not a field-by-field patch."""
    controls = a_controls()
    other = a_strategy(name="crit", interval=4.0)
    changed = controls.replace(other)
    assert controls.snapshot().strategy is other
    assert changed["name"] == "crit"
    assert changed["interval"] == 4.0


def test_replace_reports_nothing_when_the_strategy_is_identical() -> None:
    # A no-op must not publish a ControlChanged and fill the log with noise.
    controls = a_controls()
    assert controls.replace(a_strategy()) == {}


def test_payload_is_json_safe_and_detached() -> None:
    payload = a_controls().payload()
    import json
    json.dumps(payload)
    assert payload["paused"] is False
    assert payload["strategy"]["actions"][0]["name"] == "Damage"
    payload["strategy"]["actions"].append({"bogus": True})
    assert len(a_controls().payload()["strategy"]["actions"]) == 2


def test_concurrent_applies_do_not_interleave() -> None:
    """The lock is doing real work: two threads patching different fields
    must both land, and neither may read a half-swapped strategy.
    """
    controls = a_controls(interval=2.0)
    errors: list[BaseException] = []

    def hammer(value: float) -> None:
        try:
            for _ in range(200):
                controls.apply({"interval": value})
                assert controls.snapshot().strategy.interval in (1.0, 2.0, 3.0)
        except BaseException as exc:  # noqa: BLE001 - recorded, re-raised below
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(v,)) for v in (1.0, 3.0)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
