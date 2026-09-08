"""The shopping half of a strategy.

Everything here is a value the browser can POST, so every field is typed and
range-checked the way ActionRule's are. `enabled="no"` is truthy, and a
shopping policy that spends coins on a truthy string is a real bug, not a
validation nicety.
"""

import pytest

import strategy as strategy_mod
from strategy import CardPolicy, ControlError, Shopping, ShoppingRule, Strategy


def a_rule(**over):
    base = dict(name="Damage", category="ATTACK")
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


def test_a_shopping_row_has_semantic_controls_without_template_matching_fields() -> None:
    """OCR addresses a row by name/category; target only caps purchases."""
    rule = ShoppingRule(name="Damage", category="ATTACK")
    assert rule.enabled is True
    assert rule.target is None
    assert not hasattr(rule, "template")
    assert not hasattr(rule, "threshold")
    assert not hasattr(rule, "brightness_ratio")
    assert not hasattr(rule, "layout")


def test_a_profile_carrying_a_retired_field_fails_loudly() -> None:
    """Silently ignoring a threshold someone tuned would leave them
    believing it still does something (spec §6)."""
    with pytest.raises(ControlError) as caught:
        Shopping.from_dict({
            "enabled": True,
            "workshop": [{"name": "Damage", "category": "ATTACK",
                          "threshold": 0.95}],
        })
    assert "threshold" in str(caught.value)


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
    with pytest.raises(ControlError) as exc:
        CardPolicy(max_per_visit=0)
    assert exc.value.field == "max_per_visit"


# -- limits ----------------------------------------------------------------
def test_a_visit_needs_a_tap_budget() -> None:
    with pytest.raises(ControlError) as exc:
        Shopping(max_taps_per_visit=0)
    assert exc.value.field == "max_taps_per_visit"


def test_visit_cadence_is_at_least_every_run() -> None:
    with pytest.raises(ControlError) as exc:
        Shopping(visit_every_n_runs=0)
    assert exc.value.field == "visit_every_n_runs"


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


# -- the shipped default (Task 10) ------------------------------------------
def test_the_shipped_default_ships_disarmed() -> None:
    """Nobody should be able to clone this repo and have it spend coins."""
    default = Strategy.from_config()
    assert default.shopping.armed is False


def test_the_shipped_default_leads_with_the_unlock_tiles() -> None:
    """On a fresh account every upgrade the community names first is behind
    one of these, and all three together cost 165 coins."""
    rows = [r.name for r in Strategy.from_config().shopping.workshop if r.enabled]
    assert rows[:3] == [
        "Unlock Cash Bonuses", "Unlock Defense Upgrades", "Unlock Range Upgrades"
    ]


def test_the_crit_rows_ship_switched_off() -> None:
    """Present so there is a row to switch on; off because the community is
    unanimous that crit costs more and scales slower early."""
    by_name = {r.name: r for r in Strategy.from_config().shopping.workshop}
    assert by_name["Critical Chance"].enabled is False
    assert by_name["Critical Factor"].enabled is False


def test_the_shipped_default_never_spends_gems() -> None:
    assert Strategy.from_config().shopping.cards.enabled is False


def test_the_shipped_order_matches_the_guide_page() -> None:
    """The Guide page tells the user the bot buys the three unlock tiles
    first because every upgrade the community names first is behind them.
    If this order changes without the Guide changing, the dashboard starts
    lying to the person reading it."""
    rows = [r.name for r in Strategy.from_config().shopping.workshop]
    assert rows[:3] == [
        "Unlock Cash Bonuses", "Unlock Defense Upgrades", "Unlock Range Upgrades"
    ]
    assert rows.index("Health") < rows.index("Damage"), "defence before attack"


# -- an unlimited coin budget ----------------------------------------------
def test_a_null_coin_budget_means_unlimited() -> None:
    """`0` already means "spend nothing", so "no limit" needs its own value.
    `None` is the same word the rest of this config already speaks:
    Strategy.max_runs and ShoppingRule.target both spell "no cap" that way."""
    assert Shopping(coin_budget=None).coin_budget is None


def test_an_unlimited_coin_budget_round_trips_through_a_stored_profile() -> None:
    raw = Shopping(coin_budget=None).to_dict()
    assert raw["coin_budget"] is None
    assert Shopping.from_dict(raw).coin_budget is None


def test_a_coin_budget_of_zero_still_means_spend_nothing() -> None:
    """The one thing "unlimited" must not do is change what every profile on
    disk already says."""
    assert Shopping(coin_budget=0).coin_budget == 0


# -- a budget expressed as a share of the wallet ---------------------------
@pytest.mark.parametrize("value", [0, -0.1, 1.01, 2])
def test_a_wallet_share_outside_a_share_is_refused(value) -> None:
    """0 would refuse every purchase forever without saying so, and anything
    above 1 is not a share of anything."""
    with pytest.raises(ControlError):
        Shopping(coin_budget_pct=value)


@pytest.mark.parametrize("value", [None, 0.01, 0.25, 1])
def test_a_wallet_share_within_a_share_is_accepted(value) -> None:
    assert Shopping(coin_budget_pct=value).coin_budget_pct == value


def test_a_wallet_share_survives_the_round_trip_the_browser_posts() -> None:
    """The editor POSTs the whole shopping object, so a field that does not
    survive to_dict is a field the next save silently deletes."""
    original = Shopping(coin_budget_pct=.25)
    assert Shopping.from_dict(original.to_dict()).coin_budget_pct == .25


def test_a_wallet_share_that_is_not_a_number_is_refused() -> None:
    with pytest.raises(ControlError):
        Shopping(coin_budget_pct="a quarter")
