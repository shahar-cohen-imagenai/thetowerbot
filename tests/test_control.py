"""Controls is the one mutable thing the web layer may touch."""

from __future__ import annotations

import threading

import pytest

from control import ControlError, Controls


def test_snapshot_is_json_safe_and_detached() -> None:
    controls = Controls(enabled_actions={"Damage"})
    snap = controls.snapshot()
    assert isinstance(snap["enabled_actions"], list)
    # Mutating the snapshot must not reach back into the live object: the
    # whole point of snapshotting is that the loop and the web thread never
    # share a mutable structure.
    snap["enabled_actions"].append("Tier")
    assert controls.snapshot()["enabled_actions"] == ["Damage"]


def test_apply_returns_only_what_changed() -> None:
    controls = Controls(paused=False, interval=2.0)
    changed = controls.apply({"paused": True, "interval": 2.0})
    assert changed == {"paused": True}
    assert controls.snapshot()["paused"] is True


def test_apply_ignores_unknown_fields() -> None:
    controls = Controls()
    assert controls.apply({"nonsense": 1}) == {}


def test_interval_must_be_positive_and_sane() -> None:
    controls = Controls(interval=2.0)
    with pytest.raises(ControlError) as caught:
        controls.apply({"interval": 0})
    assert caught.value.field == "interval"
    # A rejected patch changes nothing at all.
    assert controls.snapshot()["interval"] == 2.0

    with pytest.raises(ControlError):
        controls.apply({"interval": 3601})


def test_strategy_must_be_known() -> None:
    controls = Controls()
    with pytest.raises(ControlError) as caught:
        controls.apply({"strategy": "vibes"})
    assert caught.value.field == "strategy"


def test_enabled_actions_must_be_a_list_of_strings() -> None:
    controls = Controls(enabled_actions={"Damage"})
    assert controls.apply({"enabled_actions": ["Damage", "Tier"]}) == {
        "enabled_actions": ["Damage", "Tier"]
    }
    with pytest.raises(ControlError):
        controls.apply({"enabled_actions": "Damage"})


def test_a_partial_patch_that_fails_late_changes_nothing() -> None:
    """Validate everything, then commit - never half-apply."""
    controls = Controls(paused=False, interval=2.0)
    with pytest.raises(ControlError):
        controls.apply({"paused": True, "interval": -1})
    assert controls.snapshot()["paused"] is False


def test_concurrent_applies_do_not_corrupt_state() -> None:
    controls = Controls(interval=2.0)
    errors: list[Exception] = []

    def hammer(value: float) -> None:
        try:
            for _ in range(200):
                controls.apply({"interval": value})
                controls.snapshot()
        except Exception as exc:  # noqa: BLE001 - the test is what it catches
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(v,)) for v in (1.0, 5.0)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert controls.snapshot()["interval"] in (1.0, 5.0)
