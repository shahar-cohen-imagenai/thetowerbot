from __future__ import annotations

import pytest

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
