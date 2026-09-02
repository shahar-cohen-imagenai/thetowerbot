"""The strategy value objects: a Strategy that exists is a Strategy in bounds.

Range and structure checks live in __post_init__ rather than in a validate()
someone can forget to call, so an out-of-range Strategy is unconstructible
rather than merely discouraged. Template existence is NOT checked here - it
touches the disk, so it lives in validated() and is tested in Task 2.
"""

from __future__ import annotations

import dataclasses

import pytest

import config
from strategy import ActionRule, ControlError, Strategy


def a_strategy(**overrides) -> Strategy:
    """A valid Strategy, with named fields overridden per test."""
    base = dict(
        name="test",
        actions=(ActionRule(name="Damage", template="upgrade_damage.png"),),
    )
    return Strategy(**{**base, **overrides})


def test_from_config_mirrors_the_shipped_actions() -> None:
    strategy = Strategy.from_config()
    assert strategy.name == "default"
    assert [rule.name for rule in strategy.actions] == [
        action.name for action in config.ACTIONS
    ]
    # Order is priority, so the round-trip must preserve it exactly.
    assert [rule.template for rule in strategy.actions] == [
        action.template for action in config.ACTIONS
    ]
    assert strategy.actions[0].threshold == config.ACTIONS[0].threshold


def test_a_rule_converts_to_the_action_the_loop_already_takes() -> None:
    rule = ActionRule(
        name="Damage", template="upgrade_damage.png",
        threshold=0.95, brightness_ratio=0.5,
    )
    action = rule.as_action()
    assert isinstance(action, config.Action)
    assert action.name == "Damage"
    assert action.template == "upgrade_damage.png"
    assert action.threshold == 0.95
    assert action.brightness_ratio == 0.5


def test_rules_and_strategies_are_frozen() -> None:
    # Frozen all the way down is what lets snapshot() stop copying: a caller
    # who is handed one cannot reach back into live state through it.
    strategy = a_strategy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        strategy.interval = 5.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        strategy.actions[0].threshold = 0.5


@pytest.mark.parametrize("interval", [0.0, 0.05, 3601.0, -1.0])
def test_interval_must_be_in_range(interval: float) -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(interval=interval)
    assert caught.value.field == "interval"


@pytest.mark.parametrize("threshold", [0.0, -0.1, 1.01])
def test_threshold_must_be_a_normalised_score(threshold: float) -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(actions=(ActionRule(name="D", template="d.png", threshold=threshold),))
    assert caught.value.field == "threshold"


def test_brightness_ratio_of_zero_is_legal() -> None:
    # config documents 0.0 as "disable the check", so it must not be rejected
    # along with the genuinely out-of-range values.
    strategy = a_strategy(
        actions=(ActionRule(name="D", template="d.png", brightness_ratio=0.0),)
    )
    assert strategy.actions[0].brightness_ratio == 0.0


@pytest.mark.parametrize("ratio", [-0.1, 1.5])
def test_brightness_ratio_must_be_a_fraction(ratio: float) -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(actions=(ActionRule(name="D", template="d.png", brightness_ratio=ratio),))
    assert caught.value.field == "brightness_ratio"


@pytest.mark.parametrize(
    "field,value",
    [
        ("click_cooldown", -1.0),
        ("click_cooldown", 61.0),
        ("navigation_cooldown", -1.0),
        ("navigation_cooldown", 61.0),
        ("screen_confirmations", 0),
        ("screen_confirmations", 11),
        ("max_runs", 0),
        ("max_runs", -5),
        ("affordability", "vibes"),
    ],
)
def test_out_of_range_fields_name_themselves(field: str, value: object) -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(**{field: value})
    # The field name is what the browser renders beside the offending input,
    # so it has to be the real one, not a generic "invalid strategy".
    assert caught.value.field == field


def test_zero_cooldowns_are_legal() -> None:
    strategy = a_strategy(click_cooldown=0.0, navigation_cooldown=0.0)
    assert strategy.click_cooldown == 0.0


def test_max_runs_may_be_unlimited() -> None:
    assert a_strategy(max_runs=None).max_runs is None
    assert a_strategy(max_runs=1).max_runs == 1


def test_actions_must_be_non_empty() -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(actions=())
    assert caught.value.field == "actions"


def test_action_names_must_be_unique() -> None:
    # A duplicate name makes Tapped.action ambiguous in the event log and in
    # the per-action tap tallies, which key on it.
    with pytest.raises(ControlError) as caught:
        a_strategy(actions=(
            ActionRule(name="Damage", template="a.png"),
            ActionRule(name="Damage", template="b.png"),
        ))
    assert caught.value.field == "actions"


def test_actions_are_normalised_to_a_tuple() -> None:
    # from_dict hands in a list; the loop must never receive something a
    # caller could append to.
    strategy = a_strategy(actions=[ActionRule(name="D", template="d.png")])
    assert isinstance(strategy.actions, tuple)
