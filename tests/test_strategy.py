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


@pytest.mark.parametrize(
    "field,value",
    [
        ("interval", 0.1),
        ("interval", 3600.0),
        ("click_cooldown", 60.0),
        ("navigation_cooldown", 60.0),
        ("screen_confirmations", 1),
        ("screen_confirmations", 10),
        ("max_runs", 1),
    ],
)
def test_the_bounds_themselves_are_accepted(field: str, value: object) -> None:
    """Every limit is inclusive, and only an accept case can prove it.

    The reject cases above pass just as happily against a `<` where a `<=`
    belongs - they only ever sit outside the bound. These sit exactly on it.
    """
    assert getattr(a_strategy(**{field: value}), field) == value


@pytest.mark.parametrize("threshold", [0.01, 1.0])
def test_a_threshold_at_its_bound_is_accepted(threshold: float) -> None:
    # 1.0 is a demand for a perfect correlation - unwise, but not invalid.
    rule = ActionRule(name="D", template="d.png", threshold=threshold)
    assert rule.threshold == threshold


def test_brightness_ratio_of_one_is_legal() -> None:
    rule = ActionRule(name="D", template="d.png", brightness_ratio=1.0)
    assert rule.brightness_ratio == 1.0


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", "no"),        # truthy string: the row would keep firing
        ("name", 123),
        ("template", 456),
        ("threshold", "high"),
    ],
)
def test_a_rule_rejects_a_field_of_the_wrong_type(field: str, value: object) -> None:
    """Dataclasses do not check types, and this one is fed client JSON.

    enabled="no" is the one that matters: it constructs, it is truthy, and a
    row the user switched OFF would go on being bought with no error anywhere.
    """
    base = dict(name="D", template="d.png")
    with pytest.raises(ControlError) as caught:
        ActionRule(**{**base, field: value})
    assert caught.value.field == field


@pytest.mark.parametrize(
    "field,value",
    [
        ("auto_navigate", "maybe"),
        ("name", 123),
        ("interval", True),               # bool subclasses int: 1 second
        ("screen_confirmations", True),
        ("max_runs", True),
        ("affordability", 7),
    ],
)
def test_a_strategy_rejects_a_field_of_the_wrong_type(field: str, value: object) -> None:
    with pytest.raises(ControlError) as caught:
        a_strategy(**{field: value})
    assert caught.value.field == field


def test_numeric_fields_still_accept_a_plain_int() -> None:
    # The bool guard must not become an int guard: interval=2 is a fine way
    # to write two seconds.
    assert a_strategy(interval=2, click_cooldown=1).interval == 2


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


def test_dict_round_trip_preserves_everything() -> None:
    original = a_strategy(
        interval=3.5, auto_navigate=True, max_runs=7,
        affordability="brightness", click_cooldown=0.5,
        navigation_cooldown=4.0, screen_confirmations=3,
        actions=(
            ActionRule(name="Damage", template="d.png", threshold=0.95),
            ActionRule(name="Speed", template="s.png", enabled=False),
        ),
    )
    assert Strategy.from_dict(original.to_dict()) == original


def test_to_dict_is_json_serialisable() -> None:
    import json
    # The store writes this straight to a file; a tuple that json cannot
    # encode would only show up at save time.
    text = json.dumps(a_strategy().to_dict())
    assert "actions" in text


def test_from_dict_rejects_an_unknown_key() -> None:
    # A typo in a hand-edited file must not be silently ignored - that is
    # exactly the case where the file says one thing and the bot does another.
    raw = a_strategy().to_dict()
    raw["intervall"] = 5.0
    with pytest.raises(ControlError) as caught:
        Strategy.from_dict(raw)
    assert caught.value.field == "intervall"


def test_from_dict_rejects_a_missing_required_key() -> None:
    raw = a_strategy().to_dict()
    del raw["actions"]
    with pytest.raises(ControlError) as caught:
        Strategy.from_dict(raw)
    assert caught.value.field == "actions"


def test_from_dict_rejects_a_wrong_type_with_the_field_name() -> None:
    raw = a_strategy().to_dict()
    raw["interval"] = "fast"
    with pytest.raises(ControlError) as caught:
        Strategy.from_dict(raw)
    assert caught.value.field == "interval"


def test_from_dict_rejects_actions_as_null() -> None:
    # Malformed actions: None instead of a list - must be caught as ControlError
    raw = a_strategy().to_dict()
    raw["actions"] = None
    with pytest.raises(ControlError) as caught:
        Strategy.from_dict(raw)
    assert caught.value.field == "actions"


def test_from_dict_rejects_actions_as_a_scalar() -> None:
    # Malformed actions: a scalar (e.g., int) instead of a list - must be caught
    raw = a_strategy().to_dict()
    raw["actions"] = 5
    with pytest.raises(ControlError) as caught:
        Strategy.from_dict(raw)
    assert caught.value.field == "actions"


def test_from_dict_rejects_actions_containing_a_non_mapping() -> None:
    # Malformed action entry: a scalar instead of a dict - must be caught
    raw = a_strategy().to_dict()
    raw["actions"] = [123]
    with pytest.raises(ControlError) as caught:
        Strategy.from_dict(raw)
    assert caught.value.field == "actions"


def test_validated_accepts_templates_that_exist(tmp_path) -> None:
    (tmp_path / "d.png").write_bytes(b"")
    strategy = a_strategy(actions=(ActionRule(name="D", template="d.png"),))
    assert strategy.validated(template_dir=tmp_path) is strategy


def test_validated_rejects_a_missing_template(tmp_path) -> None:
    """A mistyped filename does not fail loudly on its own.

    TemplateCache.get() raises when the loop first reaches that row, deep
    inside a scan, and the bot then reports an error every pass forever.
    Rejecting it at save turns a recurring runtime failure into one 422.
    """
    strategy = a_strategy(actions=(ActionRule(name="D", template="nope.png"),))
    with pytest.raises(ControlError) as caught:
        strategy.validated(template_dir=tmp_path)
    assert caught.value.field == "template"
    assert "nope.png" in str(caught.value)


def test_validated_checks_disabled_rows_too(tmp_path) -> None:
    # A disabled row is one checkbox away from running. Letting it hold a
    # broken template just moves the failure to whenever it gets switched on.
    strategy = a_strategy(
        actions=(ActionRule(name="D", template="nope.png", enabled=False),)
    )
    with pytest.raises(ControlError):
        strategy.validated(template_dir=tmp_path)


def test_validated_accepts_a_template_in_a_subdirectory(tmp_path) -> None:
    """Containment, not "no separators" - config.NAV_TARGETS uses subfolders.

    This is the over-tightening guard: a rule that banned "/" outright would
    pass every escape test above and still break "nav/claim.png".
    """
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.png").write_bytes(b"")
    strategy = a_strategy(actions=(ActionRule(name="D", template="sub/x.png"),))
    assert strategy.validated(template_dir=tmp_path) is strategy


@pytest.mark.parametrize("template", ["/etc/passwd", "../secret.png", "sub/../../out.png"])
def test_validated_refuses_a_template_outside_the_directory(template, tmp_path) -> None:
    """A template is client input joined to a path, exactly like a name.

    Path.__truediv__ discards the left side when the right is absolute, so an
    unchecked absolute template is passed to cv2.imread unmodified - the
    request body would choose which file on disk the bot reads.
    """
    # The escape targets are made to exist, so it is containment being
    # tested and not merely "the file is missing".
    (tmp_path.parent / "secret.png").write_bytes(b"")
    (tmp_path.parent / "out.png").write_bytes(b"")
    strategy = a_strategy(actions=(ActionRule(name="D", template=template),))
    with pytest.raises(ControlError) as caught:
        strategy.validated(template_dir=tmp_path)
    assert caught.value.field == "template"
    assert "inside" in str(caught.value)


def test_validated_refuses_a_non_string_template(tmp_path) -> None:
    """A non-string template used to reach Path.__truediv__ as a TypeError.

    ActionRule now rejects one at construction, so this reaches validated()
    only by going around __post_init__ - which is the point: the guard has to
    hold for whatever self actually contains, and an untested guard is one
    the next reader deletes.
    """
    rule = ActionRule(name="D", template="d.png")
    object.__setattr__(rule, "template", 123)
    with pytest.raises(ControlError) as caught:
        a_strategy(actions=(rule,)).validated(template_dir=tmp_path)
    assert caught.value.field == "template"


def test_the_shipped_default_passes_its_own_template_check() -> None:
    # If this fails, config.ACTIONS references a template that is not in the
    # repo - which would make ensure_seeded() write an unloadable profile.
    Strategy.from_config().validated()
