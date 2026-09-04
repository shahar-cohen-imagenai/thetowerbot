from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import db
import events
import ledger


def test_a_workshop_purchase_debits_coins() -> None:
    line = ledger.classify(
        events.Purchased(item="Health", category="DEFENSE", price=75,
                         coins_before=1770, dry_run=False, seq=7, ts=1000.0)
    )

    assert line is not None
    assert (line.kind, line.currency, line.delta) == ("WORKSHOP_BUY", "coins", -75)
    assert (line.price, line.observed, line.seq) == (75, 1770, 7)


def test_a_card_purchase_debits_gems() -> None:
    line = ledger.classify(
        events.Purchased(item="x1", category="CARDS", price=20,
                         gems_before=40, dry_run=False, seq=8, ts=1000.0)
    )

    assert line is not None
    assert (line.kind, line.currency, line.delta) == ("CARD_BUY", "gems", -20)
    assert line.observed == 40


def test_a_rehearsal_records_the_price_but_moves_nothing() -> None:
    """delta 0, not -price: a dry run never reached device.tap, so no coins
    left the account. price still records what it would have cost."""
    line = ledger.classify(
        events.Purchased(item="Health", category="DEFENSE", price=75,
                         coins_before=1770, dry_run=True, seq=9, ts=1000.0)
    )

    assert line is not None
    assert (line.delta, line.price, line.dry_run) == (0, 75, True)


def test_an_unreadable_price_leaves_the_movement_unknown() -> None:
    """None, not 0. Zero means "provably moved nothing"; this purchase did
    move coins, by an amount nobody read."""
    line = ledger.classify(
        events.Purchased(item="Health", category="DEFENSE", price=None,
                         coins_before=1770, dry_run=False, seq=10, ts=1000.0)
    )

    assert line is not None
    assert line.delta is None and line.price is None


def test_a_skip_moves_nothing_and_names_the_currency_it_would_have_spent() -> None:
    line = ledger.classify(
        events.PurchaseSkipped(item="Damage", reason="unaffordable",
                               coins_before=1770, seq=11, ts=1000.0)
    )

    assert line is not None
    assert (line.kind, line.currency, line.delta) == ("BUY_SKIPPED", "coins", 0)
    assert (line.observed, line.reason) == (1770, "unaffordable")


def test_a_card_skip_reconciles_against_gems() -> None:
    line = ledger.classify(
        events.PurchaseSkipped(item="x10", reason="capped", detail="gem floor",
                               gems_before=40, seq=12, ts=1000.0)
    )

    assert line is not None
    assert (line.currency, line.observed) == ("gems", 40)


def test_a_run_payout_credits_the_coins_it_earned() -> None:
    line = ledger.classify(
        events.RunEnded(run_id=4, duration=300.0, wave=10, coins=350, tier=1,
                        seq=13, ts=1000.0)
    )

    assert line is not None
    assert (line.kind, line.currency, line.delta) == ("RUN_PAYOUT", "coins", 350)
    assert line.run_id == 4


def test_an_unreadable_payout_credits_nothing_known() -> None:
    line = ledger.classify(
        events.RunEnded(run_id=4, duration=300.0, wave=10, coins=None, tier=1,
                        seq=14, ts=1000.0)
    )

    assert line is not None and line.delta is None


@pytest.mark.parametrize(
    "event",
    [
        events.ShoppingStarted(visit=1, dry_run=False),
        events.ShoppingEnded(visit=1, bought=2, spent=95),
        events.ShoppingUnavailable(reason="header atlas incomplete"),
        events.ControlChanged(changed={"paused": True}),
    ],
)
def test_the_non_financial_lines_take_no_part_in_the_arithmetic(
    event: events.Event,
) -> None:
    line = ledger.classify(event)

    assert line is not None
    assert line.currency is None


@pytest.mark.parametrize(
    "event",
    [
        events.Tapped(action="Damage", x=1, y=2, score=0.9),
        events.Skipped(action="Damage", reason="cooldown"),
        events.ScanCompleted(screen="IN_RUN", duration_ms=12.0),
        events.ScreenChanged(prev="MAIN_MENU", curr="IN_RUN", confidence=0.9, scores={}),
        events.PageChanged(prev_page="MAIN_MENU", curr_page="WORKSHOP", confidence=0.9),
        events.Navigated(target="BATTLE"),
        events.RunStarted(run_id=1),
        events.BotError(message="boom"),
        events.UnknownScreen(snapshot_path="x.png", best_anchor="a", best_score=0.1),
    ],
)
def test_in_run_and_diagnostic_events_are_not_ledger_lines(
    event: events.Event,
) -> None:
    """The ledger is the non-battle history. In-run upgrades are bought with
    per-run cash that resets, so they are not account history at all."""
    assert ledger.classify(event) is None


def writer(tmp_path: Path) -> tuple[ledger.LedgerWriter, sqlite3.Connection]:
    conn = db.connect(tmp_path / "bot.db")
    return ledger.LedgerWriter(conn), conn


def test_a_matching_reading_produces_one_line_and_no_adjustment(tmp_path: Path) -> None:
    write, _ = writer(tmp_path)

    first = write.lines_for(
        events.Purchased(item="Health", category="DEFENSE", price=75,
                         coins_before=1770, dry_run=False, seq=1, ts=1.0)
    )
    second = write.lines_for(
        events.Purchased(item="Damage", category="ATTACK", price=50,
                         coins_before=1695, dry_run=False, seq=2, ts=2.0)
    )

    assert [line.kind for line in first] == ["WORKSHOP_BUY"]
    assert first[0].balance_after == 1695
    assert [line.kind for line in second] == ["WORKSHOP_BUY"]
    assert second[0].balance_after == 1645


def test_a_balance_that_moved_behind_the_bot_gets_its_own_line(tmp_path: Path) -> None:
    """A lab slot bought by hand is the motivating case: the bot cannot see
    the Labs screen at all, so the only trace is gems that went missing."""
    write, _ = writer(tmp_path)

    write.lines_for(
        events.Purchased(item="x1", category="CARDS", price=20,
                         gems_before=200, dry_run=False, seq=1, ts=1.0)
    )
    lines = write.lines_for(
        events.Purchased(item="x1", category="CARDS", price=20,
                         gems_before=40, dry_run=False, seq=2, ts=2.0)
    )

    assert [line.kind for line in lines] == ["UNEXPLAINED", "CARD_BUY"]
    adjustment = lines[0]
    assert (adjustment.currency, adjustment.delta) == ("gems", -140)
    assert adjustment.balance_after == 40
    assert adjustment.seq is None
    # Dated to the event that REVEALED it, not to when the spend happened -
    # which nobody knows.
    assert adjustment.ts == 2.0
    assert lines[1].balance_after == 20


def test_coins_and_gems_reconcile_independently(tmp_path: Path) -> None:
    write, _ = writer(tmp_path)

    write.lines_for(
        events.Purchased(item="Health", category="DEFENSE", price=75,
                         coins_before=1770, dry_run=False, seq=1, ts=1.0)
    )
    lines = write.lines_for(
        events.Purchased(item="x1", category="CARDS", price=20,
                         gems_before=40, dry_run=False, seq=2, ts=2.0)
    )

    # The first gem reading ever seen establishes the chain; it cannot
    # contradict a coin balance.
    assert [line.kind for line in lines] == ["CARD_BUY"]
    assert lines[0].balance_after == 20


def test_the_first_reading_of_a_currency_never_looks_unexplained(tmp_path: Path) -> None:
    write, _ = writer(tmp_path)

    lines = write.lines_for(
        events.Purchased(item="Health", category="DEFENSE", price=75,
                         coins_before=1770, dry_run=False, seq=1, ts=1.0)
    )

    assert [line.kind for line in lines] == ["WORKSHOP_BUY"]


def test_a_hole_from_an_unreadable_price_is_closed_by_the_next_reading(
    tmp_path: Path,
) -> None:
    write, _ = writer(tmp_path)

    write.lines_for(
        events.Purchased(item="Health", category="DEFENSE", price=75,
                         coins_before=1770, dry_run=False, seq=1, ts=1.0)
    )
    hole = write.lines_for(
        events.Purchased(item="Damage", category="ATTACK", price=None,
                         coins_before=1695, dry_run=False, seq=2, ts=2.0)
    )
    after = write.lines_for(
        events.PurchaseSkipped(item="Damage", reason="unaffordable",
                               coins_before=1600, seq=3, ts=3.0)
    )

    # The purchase moved coins by an amount nobody read, so the chain breaks.
    assert hole[0].balance_after is None
    # ...and the next reading turns the whole gap into one honest line.
    assert [line.kind for line in after] == ["UNEXPLAINED", "BUY_SKIPPED"]
    assert after[0].delta == -95
    assert after[1].balance_after == 1600


def test_a_rehearsal_leaves_the_balance_exactly_where_it_was(tmp_path: Path) -> None:
    write, _ = writer(tmp_path)

    lines = write.lines_for(
        events.Purchased(item="Health", category="DEFENSE", price=75,
                         coins_before=1770, dry_run=True, seq=1, ts=1.0)
    )

    assert lines[0].balance_after == 1770


def test_a_run_payout_credits_the_running_coin_balance(tmp_path: Path) -> None:
    write, _ = writer(tmp_path)

    write.lines_for(
        events.Purchased(item="Health", category="DEFENSE", price=75,
                         coins_before=1770, dry_run=False, seq=1, ts=1.0)
    )
    lines = write.lines_for(
        events.RunEnded(run_id=1, duration=300.0, wave=10, coins=350, tier=1,
                        seq=2, ts=2.0)
    )

    assert lines[0].balance_after == 2045


def test_a_writer_opened_over_an_existing_ledger_resumes_its_balances(
    tmp_path: Path,
) -> None:
    """A restart must not invent a bogus UNEXPLAINED by starting from zero."""
    first, conn = writer(tmp_path)
    for line in first.lines_for(
        events.Purchased(item="Health", category="DEFENSE", price=75,
                         coins_before=1770, dry_run=False, seq=1, ts=1.0)
    ):
        db.insert_ledger(conn, line.as_row())

    resumed = ledger.LedgerWriter(conn)
    lines = resumed.lines_for(
        events.Purchased(item="Damage", category="ATTACK", price=50,
                         coins_before=1695, dry_run=False, seq=2, ts=2.0)
    )

    assert [line.kind for line in lines] == ["WORKSHOP_BUY"]


def test_an_event_outside_the_catalog_produces_no_lines(tmp_path: Path) -> None:
    write, _ = writer(tmp_path)

    assert write.lines_for(events.Tapped(action="Damage", x=1, y=2, score=0.9)) == []


def test_backfill_replays_stored_events_into_lines(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "bot.db")
    db.insert_event(conn, {
        "seq": 1, "run_id": None, "ts": 1.0, "type": "Purchased",
        "screen": None, "action": None, "reason": None, "score": None,
        "price": 75, "wallet": None,
        "detail": '{"item": "Health", "category": "DEFENSE", '
                  '"coins_before": 1770, "gems_before": null, "dry_run": false}',
    })

    written = ledger.backfill(conn)

    assert written == 1
    line = db.ledger_page(conn)[0]
    assert (line["kind"], line["item"], line["delta"]) == ("WORKSHOP_BUY", "Health", -75)
    assert line["balance_after"] == 1695


def test_backfill_is_a_one_time_migration_not_a_repair(tmp_path: Path) -> None:
    """Derived UNEXPLAINED lines have no seq, so the unique index cannot
    dedupe them - a second unguarded replay would duplicate every one."""
    conn = db.connect(tmp_path / "bot.db")
    db.insert_event(conn, {
        "seq": 1, "run_id": None, "ts": 1.0, "type": "Purchased",
        "screen": None, "action": None, "reason": None, "score": None,
        "price": 75, "wallet": None,
        "detail": '{"item": "Health", "category": "DEFENSE", '
                  '"coins_before": 1770, "gems_before": null, "dry_run": false}',
    })

    assert ledger.backfill(conn) == 1
    assert ledger.backfill(conn) == 0
    assert len(db.ledger_page(conn)) == 1


def test_backfill_over_an_empty_events_table_writes_nothing(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "bot.db")

    assert ledger.backfill(conn) == 0
