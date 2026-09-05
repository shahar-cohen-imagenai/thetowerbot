"""The transaction journal - what the bot meant to do, and what it proved.

These tests exist because of one window in the buy path: between the tap
that spends coins and the `Purchased` event that records it, nothing is
written to disk. A process that dies in that window has spent the money and
kept no record of it, so the next start is free to spend it again.

Every test here drives real sqlite on a real file, because the whole point
of the module is what survives the process that wrote it.
"""

from __future__ import annotations

import pytest

import transactions


def _intent(**overrides) -> transactions.Intent:
    """A readable, affordable workshop row - the uninteresting case."""
    fields = {
        "item": "Damage",
        "category": "ATTACK",
        "currency": "coins",
        "price": 10,
        "wallet_before": 13,
        "evidence": frozenset({"damage", "attack_speed"}),
        "ts": 1.0,
    }
    fields.update(overrides)
    return transactions.Intent(**fields)


def test_an_intent_survives_the_process_that_recorded_it(tmp_path) -> None:
    """The crash case. A tap was sent and the process died before anything
    else. The record of that tap has to outlive it."""
    path = tmp_path / "bot.db"
    journal = transactions.TransactionJournal(path)

    txn = journal.open(_intent())
    journal.record_action(txn.key)
    del journal  # nothing orderly happens on the way out of a crash

    reopened = transactions.TransactionJournal(path)
    still_open = reopened.open_transactions()

    assert [t.item for t in still_open] == ["Damage"]
    assert still_open[0].stage == transactions.Stage.ACTED


def test_a_tap_that_crashed_before_confirmation_is_not_tapped_again(tmp_path) -> None:
    """The acceptance gate. The money may already be gone; a second tap
    would spend it twice. The next scan is refused the attempt outright."""
    path = tmp_path / "bot.db"
    first = transactions.TransactionJournal(path)
    txn = first.open(_intent())
    first.record_action(txn.key, at=1.0)

    restarted = transactions.TransactionJournal(path)

    with pytest.raises(transactions.TransactionInFlight):
        restarted.open(_intent(ts=2.0, wallet_before=3))


def test_a_transaction_owns_exactly_one_device_action(tmp_path) -> None:
    """One step, one tap. A retried tap inside one transaction would spend
    twice against a single record and reconcile to the wrong number."""
    journal = transactions.TransactionJournal(tmp_path / "bot.db")
    txn = journal.open(_intent())
    journal.record_action(txn.key, at=1.0)

    with pytest.raises(transactions.ActionAlreadyTaken):
        journal.record_action(txn.key, at=1.5)


def test_a_free_upgrade_confirms_the_effect_but_never_a_spend(tmp_path) -> None:
    """The acceptance gate. The upgrade really was applied - the row
    changed - but nothing left the wallet, and the history must not claim
    it did."""
    journal = transactions.TransactionJournal(tmp_path / "bot.db")
    txn = journal.open(_intent(price=0, wallet_before=13))
    journal.record_action(txn.key, at=1.0)

    outcome = journal.resolve(
        txn.key, wallet_after=13, effect_changed=True, ts=2.0
    )

    assert outcome.verdict == transactions.Verdict.FREE
    assert outcome.spent == 0


def test_a_purchase_is_bought_when_the_wallet_fell_by_what_it_cost(tmp_path) -> None:
    """The ordinary case, and the contrast that gives the free case its
    meaning: a debit of exactly the price, alongside a changed row."""
    journal = transactions.TransactionJournal(tmp_path / "bot.db")
    txn = journal.open(_intent(price=10, wallet_before=13))
    journal.record_action(txn.key, at=1.0)

    outcome = journal.resolve(
        txn.key, wallet_after=3, effect_changed=True, ts=2.0
    )

    assert outcome.verdict == transactions.Verdict.BOUGHT
    assert outcome.spent == 10


def test_income_arriving_mid_purchase_leaves_the_amount_unknown(tmp_path) -> None:
    """Coins keep arriving while a purchase is in flight. The row changed,
    so something was bought - but the wallet no longer proves how much, and
    reporting the predicted price here would invent a number."""
    journal = transactions.TransactionJournal(tmp_path / "bot.db")
    txn = journal.open(_intent(price=10, wallet_before=13))
    journal.record_action(txn.key, at=1.0)

    # 10 spent and 5 earned in between: a drop of 5, not of 10.
    outcome = journal.resolve(
        txn.key, wallet_after=8, effect_changed=True, ts=2.0
    )

    assert outcome.verdict == transactions.Verdict.BOUGHT
    assert outcome.spent is None


def test_a_tap_that_changed_nothing_is_refuted_rather_than_left_open(tmp_path) -> None:
    """A swallowed tap. The row did not change and the wallet did not move,
    which together prove the money is still there - the one case where
    trying again is safe."""
    journal = transactions.TransactionJournal(tmp_path / "bot.db")
    txn = journal.open(_intent(price=10, wallet_before=13))
    journal.record_action(txn.key, at=1.0)

    outcome = journal.resolve(
        txn.key, wallet_after=13, effect_changed=False, ts=2.0
    )

    assert outcome.verdict == transactions.Verdict.REFUTED
    assert outcome.spent == 0
