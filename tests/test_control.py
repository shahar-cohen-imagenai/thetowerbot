"""Controls is the one mutable thing the web layer may touch.

Reshaped: it now holds session state (paused) plus one Strategy swapped
whole. The lock, the all-or-nothing apply(), and the changed-dict that
becomes a ControlChanged event are unchanged - those are the parts that were
already right.
"""

from __future__ import annotations

import dataclasses
import sys
import threading

import pytest

import control
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
    # The exact exception, not bare Exception: a typo'd attribute name would
    # raise AttributeError and pass a broad check while proving nothing.
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.paused = True
    with pytest.raises(dataclasses.FrozenInstanceError):
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


def test_a_rejected_patch_does_not_leave_paused_set() -> None:
    """paused is session state, not part of the Strategy, so it is staged and
    committed by a separate line - the one field the all-or-nothing guarantee
    could plausibly be true for by accident. staged_paused is computed before
    merged() can raise but assigned only after it returns; nothing else pins
    that ordering.
    """
    controls = a_controls()
    with pytest.raises(ControlError):
        controls.apply({"paused": True, "interval": -1})
    assert controls.snapshot().paused is False


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
    # A summary, not the rows: `changed` is one line in the event feed and a
    # JSON blob in the events table, and the full list makes a single toggle
    # unreadable in both. Damage is disabled by this patch, so it is absent.
    assert changed["actions"] == ["Critical Chance"]


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
    # One key, not nine: a swap is one event ("this profile is live now"),
    # not a field-by-field edit - and `name` is a key apply() can never
    # produce, so spreading it out would put two shapes on one event type.
    assert list(changed) == ["strategy"]
    assert changed["strategy"] == other.to_dict()


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


def test_a_multi_field_patch_is_never_observed_half_applied() -> None:
    """apply() commits every staged field inside one lock acquisition, and
    snapshot() reads them all inside one too - a multi-field patch is the
    thing the lock actually protects. A writer alternates between two
    internally-consistent pairs; a reader must never catch it mid-swap.

    Restored from the pre-reshape test file (git show af5fd59), adapted to
    the new API: `interval` now lives on `strategy`, not directly on
    Controls, and snapshot() returns attributes rather than dict keys.
    """
    controls = a_controls(interval=5.0)
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
                paused, interval = snap.paused, snap.strategy.interval
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


def test_apply_never_reports_or_reverts_a_field_the_caller_never_patched() -> None:
    """The regression this guards: apply() used to read `self.paused` (and
    `self.strategy`) before releasing the lock to build a candidate, so a
    concurrent apply() that landed in that window would be silently
    reverted by the next commit - and reported in `changed` as if the
    reverting patch had asked for it, which it never did.

    One thread patches only `interval`; another patches only `paused`. A
    patch that never mentions `paused` must never report it in `changed`.
    """
    controls = a_controls(interval=2.0)
    stop = threading.Event()
    errors: list[BaseException] = []
    bad_changes: list[dict] = []

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)

    def hammer_interval() -> None:
        try:
            for _ in range(3000):
                for value in (5.0, 2.0):
                    changed = controls.apply({"interval": value})
                    if "paused" in changed:
                        bad_changes.append(changed)
        except BaseException as exc:  # noqa: BLE001 - recorded, re-raised below
            errors.append(exc)
        finally:
            stop.set()

    def hammer_paused() -> None:
        try:
            while not stop.is_set():
                controls.apply({"paused": True})
                controls.apply({"paused": False})
        except BaseException as exc:  # noqa: BLE001 - recorded, re-raised below
            errors.append(exc)

    threads = [
        threading.Thread(target=hammer_interval),
        threading.Thread(target=hammer_paused),
    ]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old_interval)

    assert not errors
    assert not bad_changes, (
        f"a patch without 'paused' reported it as changed: {bad_changes[:5]}"
    )


# -- one-shot commands ------------------------------------------------------
# Settings are standing policy; a command happens once. The browser must
# never tap the device itself, so a button press lands here and the scan
# loop performs it on its next pass.


def test_a_requested_command_reaches_the_next_drain() -> None:
    controls = a_controls()
    controls.request("speed_up")
    assert controls.drain() == ("speed_up",)


def test_a_command_is_handed_out_exactly_once() -> None:
    """The scan loop drains every pass. A command that survived its drain
    would be re-performed on every subsequent scan - one button press
    walking the speed to the top."""
    controls = a_controls()
    controls.request("speed_up")
    controls.drain()
    assert controls.drain() == ()


def test_commands_drain_in_the_order_they_were_requested() -> None:
    controls = a_controls()
    controls.request("speed_up")
    controls.request("speed_down")
    assert controls.drain() == ("speed_up", "speed_down")


def test_an_unknown_command_is_refused() -> None:
    """Same rule the rest of this module follows: reject at the boundary,
    so nothing downstream has to decide what an unrecognised string means."""
    with pytest.raises(ControlError):
        a_controls().request("self_destruct")


def test_the_queue_is_bounded_so_a_held_button_cannot_bank_taps() -> None:
    """A browser can post faster than the loop scans. Without a ceiling, ten
    seconds of impatient clicking becomes a hundred queued taps that all fire
    long after the user stopped asking for them."""
    controls = a_controls()
    for _ in range(500):
        controls.request("speed_up")
    assert len(controls.drain()) <= control.MAX_QUEUED_COMMANDS
