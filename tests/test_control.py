"""Controls is the one mutable thing the web layer may touch."""

from __future__ import annotations

import sys
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


def test_constructor_does_not_alias_the_caller_s_set() -> None:
    """A caller-supplied enabled_actions set must be copied at construction,
    not aliased - otherwise a caller that keeps its reference can mutate
    Controls state behind the lock and without going through apply().
    """
    caller_set = {"Damage"}
    controls = Controls(enabled_actions=caller_set)
    caller_set.add("Tier")
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
    assert controls.apply({"enabled_actions": ["Damage", "Critical Chance"]}) == {
        "enabled_actions": ["Critical Chance", "Damage"]
    }
    with pytest.raises(ControlError):
        controls.apply({"enabled_actions": "Damage"})


def test_enabled_actions_rejects_a_name_that_is_not_a_real_action() -> None:
    """A typo (or a stale name from a config change) must be refused, not
    silently accepted - accepting it would disable every action for real
    while the dashboard still showed the typo as "enabled"."""
    controls = Controls(enabled_actions={"Damage"})
    with pytest.raises(ControlError) as caught:
        controls.apply({"enabled_actions": ["Typo"]})
    assert caught.value.field == "enabled_actions"
    assert controls.snapshot()["enabled_actions"] == ["Damage"]


def test_a_partial_patch_that_fails_late_changes_nothing() -> None:
    """Validate everything, then commit - never half-apply."""
    controls = Controls(paused=False, interval=2.0)
    with pytest.raises(ControlError):
        controls.apply({"paused": True, "interval": -1})
    assert controls.snapshot()["paused"] is False


def test_a_multi_field_patch_is_never_observed_half_applied() -> None:
    """apply() commits every staged field inside one lock acquisition, and
    snapshot() reads them all inside one too - a multi-field patch is the
    thing the lock actually protects. A writer alternates between two
    internally-consistent pairs; a reader must never catch it mid-swap.
    """
    controls = Controls(paused=False, interval=5.0)
    iterations = 5000
    stop = threading.Event()
    errors: list[Exception] = []

    # Force the interpreter to consider a thread switch far more often than
    # its 5ms default. Two threads doing nothing but tight setattr/getattr
    # loops rarely straddle the default switch granularity often enough to
    # land inside a two-field write; tightening it is what makes an
    # unlocked implementation lose reliably instead of by luck.
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)

    def write() -> None:
        try:
            for _ in range(iterations):
                controls.apply({"paused": True, "interval": 1.0})
                controls.apply({"paused": False, "interval": 5.0})
        except Exception as exc:  # noqa: BLE001 - the test is what it catches
            errors.append(exc)
        finally:
            stop.set()

    def read() -> None:
        try:
            while not stop.is_set():
                snap = controls.snapshot()
                paused, interval = snap["paused"], snap["interval"]
                assert (paused is True and interval == 1.0) or (
                    paused is False and interval == 5.0
                ), f"observed a torn pair: paused={paused!r} interval={interval!r}"
        except Exception as exc:  # noqa: BLE001 - the test is what it catches
            errors.append(exc)

    threads = [threading.Thread(target=write), threading.Thread(target=read)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(old_interval)

    assert not errors
