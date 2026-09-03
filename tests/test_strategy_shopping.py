"""The shopping half of a strategy.

Everything here is a value the browser can POST, so every field is typed and
range-checked the way ActionRule's are. `enabled="no"` is truthy, and a
shopping policy that spends coins on a truthy string is a real bug, not a
validation nicety.
"""

import dataclasses

import pytest

import config
import strategy as strategy_mod
from strategy import CardPolicy, ControlError, Shopping, ShoppingRule, Strategy


def a_rule(**over):
    base = dict(name="Damage", template="workshop/row_damage.png", category="ATTACK")
    return ShoppingRule(**{**base, **over})


# -- defaults preserve today's behaviour -----------------------------------
def test_a_strategy_with_no_shopping_key_loads_and_buys_nothing() -> None:
    """Every strategies/*.json on disk predates this field. It must still
    load, still validate, and come back with a policy that taps nothing -
    exactly the behaviour the bot had before shopping existed at all."""
    raw = Strategy.from_config().to_dict()
    del raw["shopping"]
    loaded = Strategy.from_dict(raw)
    assert loaded.shopping.enabled is False
    assert loaded.shopping.armed is False
    assert loaded.validated() is loaded


def test_shopping_round_trips_through_to_dict() -> None:
    original = Strategy.from_config()
    assert Strategy.from_dict(original.to_dict()) == original


# -- the arm switch --------------------------------------------------------
def test_enabled_and_armed_are_independent() -> None:
    """enabled=True, armed=False is the rehearsal, and it must be expressible."""
    rehearsal = Shopping(enabled=True, armed=False, workshop=(a_rule(),))
    assert rehearsal.enabled and not rehearsal.armed


@pytest.mark.parametrize("field", ["enabled", "armed"])
def test_a_truthy_string_is_not_a_switch(field: str) -> None:
    with pytest.raises(ControlError) as exc:
        Shopping(**{field: "no"})
    assert exc.value.field == field


# -- rows ------------------------------------------------------------------
def test_a_row_needs_a_known_category() -> None:
    with pytest.raises(ControlError) as exc:
        a_rule(category="ULTIMATE")
    assert exc.value.field == "category"


def test_row_names_must_be_unique() -> None:
    with pytest.raises(ControlError) as exc:
        Shopping(workshop=(a_rule(), a_rule()))
    assert exc.value.field == "workshop"


def test_a_row_threshold_of_zero_is_refused() -> None:
    """Zero matches anything, so the bot would tap wherever the template
    happened to correlate best - on a page where a tap spends coins."""
    with pytest.raises(ControlError):
        a_rule(threshold=0.0)


def test_a_row_converts_to_the_shape_the_matcher_takes() -> None:
    action = a_rule(threshold=0.95).as_action()
    assert isinstance(action, config.Action)
    assert action.name == "Damage"
    assert action.threshold == 0.95


# -- layout (Override 1) ----------------------------------------------------
def test_a_row_needs_a_known_layout() -> None:
    with pytest.raises(ControlError) as exc:
        a_rule(layout="grid")
    assert exc.value.field == "layout"


def test_layout_defaults_to_row() -> None:
    """Most buyable things are half-width upgrade rows; the full-width unlock
    tiles are the exception, so they are the ones that say so."""
    assert a_rule().layout == "row"


def test_layout_round_trips_through_to_dict() -> None:
    shopping = Shopping(workshop=(a_rule(layout="tile"),))
    assert Shopping.from_dict(shopping.to_dict()).workshop[0].layout == "tile"


# -- category order is derived, not hardcoded ------------------------------
def test_categories_come_out_in_first_appearance_order() -> None:
    shopping = Shopping(workshop=(
        a_rule(name="Unlock Cash Bonuses", category="UTILITY"),
        a_rule(name="Health", category="DEFENSE"),
        a_rule(name="Damage", category="ATTACK"),
        a_rule(name="Health Regen", category="DEFENSE"),
    ))
    assert shopping.categories_in_priority_order() == ("UTILITY", "DEFENSE", "ATTACK")


def test_reordering_rows_reorders_the_tabs_visited() -> None:
    shopping = Shopping(workshop=(
        a_rule(name="Damage", category="ATTACK"),
        a_rule(name="Health", category="DEFENSE"),
    ))
    assert shopping.categories_in_priority_order()[0] == "ATTACK"


def test_disabled_rows_do_not_pull_in_their_category() -> None:
    """Visiting a tab whose every row is switched off is wasted taps."""
    shopping = Shopping(workshop=(
        a_rule(name="Damage", category="ATTACK", enabled=False),
        a_rule(name="Health", category="DEFENSE"),
    ))
    assert shopping.categories_in_priority_order() == ("DEFENSE",)


def test_rows_for_a_category_keeps_priority_order_and_drops_disabled() -> None:
    shopping = Shopping(workshop=(
        a_rule(name="Damage", category="ATTACK"),
        a_rule(name="Attack Speed", category="ATTACK", enabled=False),
        a_rule(name="Critical Chance", category="ATTACK"),
    ))
    assert [r.name for r in shopping.rows_for("ATTACK")] == ["Damage", "Critical Chance"]


# -- cards -----------------------------------------------------------------
def test_the_gem_floor_may_not_be_negative() -> None:
    with pytest.raises(ControlError) as exc:
        CardPolicy(gem_floor=-1)
    assert exc.value.field == "gem_floor"


def test_a_gem_floor_of_zero_is_legal() -> None:
    """Spend every gem is a real, if bold, choice - unlike a negative floor,
    which is not a choice at all."""
    assert CardPolicy(gem_floor=0).gem_floor == 0


def test_the_batch_size_must_be_one_the_game_offers() -> None:
    with pytest.raises(ControlError) as exc:
        CardPolicy(batch="x100")
    assert exc.value.field == "batch"


def test_a_visit_must_buy_at_least_one_card_when_it_buys_any() -> None:
    with pytest.raises(ControlError):
        CardPolicy(max_per_visit=0)


# -- limits ----------------------------------------------------------------
def test_a_visit_needs_a_tap_budget() -> None:
    with pytest.raises(ControlError) as exc:
        Shopping(max_taps_per_visit=0)
    assert exc.value.field == "max_taps_per_visit"


def test_visit_cadence_is_at_least_every_run() -> None:
    with pytest.raises(ControlError):
        Shopping(visit_every_n_runs=0)


# -- patching --------------------------------------------------------------
def test_shopping_can_be_patched_through_merged() -> None:
    before = Strategy.from_config()
    after = before.merged({"shopping": {"enabled": True, "armed": False}})
    assert after.shopping.enabled is True
    assert before.shopping.enabled is False, "merged() must not mutate"


def test_an_invalid_patch_leaves_the_original_untouched() -> None:
    before = Strategy.from_config()
    with pytest.raises(ControlError):
        before.merged({"shopping": {"cards": {"batch": "x100"}}})
    assert before.shopping.cards.batch == "x1"


def test_an_unknown_shopping_field_is_refused() -> None:
    with pytest.raises(ControlError) as exc:
        Shopping.from_dict({"enabled": True, "spend_everything": True})
    assert exc.value.field == "spend_everything"


# -- template containment --------------------------------------------------
def test_a_shopping_row_may_not_escape_the_template_directory(tmp_path) -> None:
    """Same suspicion actions get: a template arrives in client JSON and is
    joined to a path, so it must name a file INSIDE the template dir."""
    base = Strategy.from_config()
    escaped = dataclasses.replace(
        base,
        shopping=dataclasses.replace(
            base.shopping, workshop=(a_rule(template="../../etc/passwd"),)
        ),
    )
    with pytest.raises(ControlError) as exc:
        escaped.validated()
    assert exc.value.field == "template"


def test_a_disabled_shopping_row_is_still_checked() -> None:
    """A disabled row is one checkbox away from spending coins."""
    base = Strategy.from_config()
    broken = dataclasses.replace(
        base,
        shopping=dataclasses.replace(
            base.shopping,
            workshop=(a_rule(template="workshop/nope.png", enabled=False),),
        ),
    )
    with pytest.raises(ControlError):
        broken.validated()
