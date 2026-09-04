"""The shopping events, and the storage shape they land in.

The point of this file is the LAST test: every new field must ride the events
table's JSON detail blob, so adding these needs no migration. That is the
property the schema was built for and this is the first feature to use it.
"""

import json

import events
import sinks.store as store


def test_purchased_carries_what_it_cost() -> None:
    event = events.Purchased(
        item="Damage", category="ATTACK", price=30, coins_before=1770, dry_run=False
    )
    assert event.type == "Purchased"
    assert event.price == 30


def test_a_dry_run_purchase_is_distinguishable_from_a_real_one() -> None:
    """The whole rehearsal is worthless if the log cannot tell them apart."""
    rehearsed = events.Purchased(
        item="Damage", category="ATTACK", price=30, coins_before=1770, dry_run=True
    )
    real = events.Purchased(
        item="Damage", category="ATTACK", price=30, coins_before=1770, dry_run=False
    )
    assert rehearsed.dry_run is True and real.dry_run is False


def test_a_purchase_with_an_unreadable_price_is_still_expressible() -> None:
    """None, not 0. A price of zero is a free upgrade; None is 'we could not
    read it', and the two must never collapse into each other."""
    event = events.Purchased(
        item="Damage", category="ATTACK", price=None, coins_before=None, dry_run=True
    )
    assert event.price is None


def test_a_card_purchase_reports_gems_not_coins() -> None:
    """A card purchase spends gems, not coins - coins_before must not be
    reused to carry the gem balance, and gems_before must not be left at
    its default just because a workshop row never needed it.
    """
    event = events.Purchased(
        item="x1", category="CARDS", price=20, gems_before=400, dry_run=True
    )
    assert event.gems_before == 400
    assert event.coins_before is None


def test_a_workshop_purchase_reports_coins_not_gems() -> None:
    """The other half of the same rule: a row purchase spends coins, and
    gems_before must stay None rather than being reused for the wrong
    currency."""
    event = events.Purchased(
        item="Damage", category="ATTACK", price=30, coins_before=1770, dry_run=True
    )
    assert event.coins_before == 1770
    assert event.gems_before is None


def test_shopping_started_carries_no_balance_fields() -> None:
    """begin() has not read a frame yet when this publishes, so a `coins`/
    `gems` field here could only ever be None - see the event's own
    docstring for why they were dropped rather than shipped always-empty."""
    event = events.ShoppingStarted(visit=1, dry_run=True)
    assert not hasattr(event, "coins")
    assert not hasattr(event, "gems")


def test_shopping_unavailable_carries_the_disabled_reason() -> None:
    event = events.ShoppingUnavailable(reason="header atlas is missing 2, 3")
    assert event.reason == "header atlas is missing 2, 3"


def test_shopping_ended_records_an_abort_with_its_reason() -> None:
    event = events.ShoppingEnded(
        visit=3, bought=2, spent=60, aborted=True, reason="tap budget exhausted"
    )
    assert event.aborted and event.reason


def test_every_new_event_stores_without_a_migration() -> None:
    """to_row maps the typed columns and JSON-dumps the rest. Nothing here may
    need a column the events table does not already have."""
    columns = {
        "seq", "run_id", "ts", "type", "screen", "action",
        "reason", "score", "price", "wallet", "detail",
    }
    samples = [
        events.PageChanged(prev_page="MAIN_MENU", curr_page="WORKSHOP", confidence=0.99),
        events.ShoppingStarted(visit=1, dry_run=True),
        events.ShoppingUnavailable(reason="header atlas is missing 2, 3"),
        events.Purchased(
            item="Damage", category="ATTACK", price=30, coins_before=1770, dry_run=True
        ),
        events.Purchased(
            item="x1", category="CARDS", price=20, gems_before=400, dry_run=True
        ),
        events.PurchaseSkipped(item="Damage", reason="unaffordable"),
        events.ShoppingEnded(visit=1, bought=1, spent=30),
    ]
    for event in samples:
        row = store.to_row(event, run_id=None)
        assert set(row) <= columns, f"{event.type} wants a column that does not exist"
        if row["detail"] is not None:
            json.loads(row["detail"])  # must be serialisable, not just present


def test_a_purchase_price_reaches_its_typed_column() -> None:
    """price has a real column, so it must be queryable without unpacking JSON."""
    row = store.to_row(
        events.Purchased(
            item="Damage", category="ATTACK", price=30, coins_before=1770, dry_run=False
        ),
        run_id=None,
    )
    assert row["price"] == 30


def test_a_menu_page_change_does_not_land_in_the_screen_column() -> None:
    """The `screen` column means the run-lifecycle screen, and
    db.screen_histogram groups the Stats page's chart by it. A menu page
    written there would silently mix WORKSHOP and CARDS into a chart about
    IN_RUN and GAME_OVER.
    """
    row = store.to_row(
        events.PageChanged(prev_page="MAIN_MENU", curr_page="WORKSHOP", confidence=0.99),
        run_id=None,
    )
    assert row["screen"] is None
    detail = json.loads(row["detail"])
    assert detail["curr_page"] == "WORKSHOP"


def test_a_run_screen_change_still_lands_in_the_screen_column() -> None:
    """The other half of the same rule - renaming PageChanged's fields must
    not disturb ScreenChanged, which relies on that branch."""
    row = store.to_row(
        events.ScreenChanged(prev="MAIN_MENU", curr="IN_RUN", confidence=0.99, scores={}),
        run_id=None,
    )
    assert row["screen"] == "IN_RUN"
