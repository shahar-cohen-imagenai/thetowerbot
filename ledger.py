"""Turn events into ledger lines - this account's non-battle history.

Two halves, split by statefulness, because they fail differently. classify()
is pure: it owns the catalog of which event becomes which kind of line and
nothing else, so every judgment in it is testable with no database.
LedgerWriter (next task) owns the running balances and the reconciliation
rule, because remembering the last observed balance is inherently stateful.

The ledger deliberately excludes everything in-run. In-run upgrades are
bought with per-run cash that resets at the start of the next run - the
`wallet` on Tapped and ScanCompleted - which is a different currency from
`coins` and not account history in any sense. A battle contributes exactly
one line: its payout.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import db
import events

COINS = "coins"
GEMS = "gems"

# Every kind a line can carry. The last six are RESERVED and nothing emits
# them today: they are the parts of the economy the bot cannot see (labs and
# lab slots, card slots, modules, relics, ultimate weapons) plus hand-entered
# lines. They are named here so adding one later is a branch in classify(),
# not a schema change.
KINDS: tuple[str, ...] = (
    "RUN_PAYOUT",
    "WORKSHOP_BUY",
    "CARD_BUY",
    "BUY_SKIPPED",
    "VISIT_START",
    "VISIT_END",
    "SHOP_UNAVAILABLE",
    "POLICY_CHANGED",
    "UNEXPLAINED",
    "LAB",
    "CARD_SLOT",
    "MODULE",
    "RELIC",
    "UW",
    "MANUAL",
)


@dataclass(frozen=True, kw_only=True)
class LedgerLine:
    """One row of the account's history.

    `delta` and `price` are separate on purpose, and the difference is what
    makes a dry-run rehearsal safe to record: `price` is what it cost or
    would have cost, `delta` is what ACTUALLY moved.

    `delta` itself distinguishes two things a single None would collapse:

    * 0    - provably moved nothing. A skip, a rehearsal.
    * None - moved by an unknown amount. Only ever an unreadable price on a
             real purchase, or an unreadable RunEnded payout. This is what
             leaves a hole in the running balance until the next reading
             closes it, as an explicit UNEXPLAINED line.
    """

    kind: str
    ts: float
    seq: int | None = None
    item: str | None = None
    category: str | None = None
    currency: str | None = None
    delta: int | None = None
    price: int | None = None
    balance_after: int | None = None
    observed: int | None = None
    dry_run: bool = False
    run_id: int | None = None
    visit: int | None = None
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        """Flatten into a row for db.insert_ledger.

        Here rather than in db.py deliberately: db.py "knows rows, not
        events", and importing this module into it would invert that.
        Mirrors sinks/store.py's to_row for the events table.
        """
        row = dataclasses.asdict(self)
        detail = row.pop("detail")
        row["dry_run"] = int(self.dry_run)
        row["detail"] = json.dumps(detail, default=str) if detail else None
        return row


def classify(event: events.Event) -> LedgerLine | None:
    """The catalog. Returns None for every event that is not account history.

    None is the honest answer for an unrecognised event, not a guess: a new
    event type gets a branch here when someone decides what it means, and
    until then it simply is not in the ledger.
    """
    base: dict[str, Any] = {"ts": event.ts, "seq": event.seq}

    match event:
        case events.RunEnded():
            # The only thing a battle contributes. RunEnded.coins is read off
            # the game-over modal's Coins caption - coins EARNED that run, not
            # a running total - so it is a credit, not a balance reading.
            return LedgerLine(
                kind="RUN_PAYOUT",
                currency=COINS,
                delta=event.coins,
                run_id=event.run_id,
                reason="abandoned" if event.abandoned else None,
                **base,
            )

        case events.Purchased():
            cards = event.category == "CARDS"
            price = event.price
            if event.dry_run:
                delta: int | None = 0
            elif price is None:
                delta = None
            else:
                delta = -price
            return LedgerLine(
                kind="CARD_BUY" if cards else "WORKSHOP_BUY",
                item=event.item,
                category=event.category,
                currency=GEMS if cards else COINS,
                delta=delta,
                price=price,
                observed=event.gems_before if cards else event.coins_before,
                dry_run=event.dry_run,
                **base,
            )

        case events.PurchaseSkipped():
            # The currency comes from WHICH balance the event reported.
            # PurchaseSkipped carries the balance for the currency the skip
            # would have spent and leaves the other None - the same rule
            # Purchased documents - so exactly one of these is ever set.
            if event.gems_before is not None:
                currency, observed = GEMS, event.gems_before
            elif event.coins_before is not None:
                currency, observed = COINS, event.coins_before
            else:
                # A row backfilled from before those fields existed. Still a
                # real line; it just reconciles nothing.
                currency, observed = None, None
            return LedgerLine(
                kind="BUY_SKIPPED",
                item=event.item,
                currency=currency,
                # A skip provably moved nothing, which is not the same fact
                # as an unreadable price.
                delta=0,
                observed=observed,
                reason=event.reason,
                detail={"detail": event.detail} if event.detail else {},
                **base,
            )

        case events.ShoppingStarted():
            return LedgerLine(
                kind="VISIT_START", visit=event.visit, dry_run=event.dry_run, **base
            )

        case events.ShoppingEnded():
            return LedgerLine(
                kind="VISIT_END",
                visit=event.visit,
                reason=event.reason or ("aborted" if event.aborted else None),
                detail={"bought": event.bought, "spent": event.spent,
                        "aborted": event.aborted},
                **base,
            )

        case events.ShoppingUnavailable():
            return LedgerLine(kind="SHOP_UNAVAILABLE", reason=event.reason, **base)

        case events.ControlChanged():
            # Why the spending policy changed, which is often the answer to
            # "why did it stop buying that".
            return LedgerLine(
                kind="POLICY_CHANGED",
                reason=event.source,
                detail={"changed": event.changed},
                **base,
            )

    return None


class LedgerWriter:
    """Turns events into ledger lines, carrying the running balances.

    Stateful, unlike classify(), because reconciliation needs to remember
    what the last observed balance was. Seeded from the table rather than
    from zero: a restart that started from zero would read the next real
    balance as an enormous unexplained gain.

    Two pieces of state per currency, not one, and the difference matters:

    * `_known`  - the last balance actually READ off the screen, plus every
                  movement since that was itself known.
    * `_stale`  - whether an unknown movement (an unreadable price) has
                  happened since. A stale chain can no longer compute a
                  balance, but it can still reconcile: the next reading is
                  compared against `_known`, and the gap - which includes
                  whatever the unreadable purchase cost - becomes one
                  UNEXPLAINED line.

    Collapsing the two by setting `_known` to None on a hole was tried and
    is wrong: it silently discards the last certain balance, so the reading
    that should have closed the hole instead starts a fresh chain and the
    missing coins are never accounted for anywhere.

    Not thread-safe, and does not need to be - the store sink's consumer
    thread is the only caller, and it is also the database's only writer.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._known: dict[str, int | None] = db.last_balances(conn)
        # A seeded writer assumes an intact chain: last_balances only returns
        # a non-NULL balance_after, which is by definition a line that closed
        # cleanly. If the process died mid-hole, the first reading after the
        # restart still produces the right UNEXPLAINED - only a non-observing
        # event landing in between would be computed off a stale base.
        self._stale: dict[str, bool] = {COINS: False, GEMS: False}

    def lines_for(self, event: events.Event) -> list[LedgerLine]:
        """Every line this event produces, in the order they must be written.

        Usually one. Two when the balance it reports contradicts the running
        total, in which case the UNEXPLAINED line comes FIRST: the gap
        happened before the event that revealed it.
        """
        line = classify(event)
        if line is None:
            return []

        currency = line.currency
        if currency is None:
            return [line]

        out: list[LedgerLine] = []
        known = self._known.get(currency)
        stale = self._stale.get(currency, False)

        if line.observed is not None:
            if known is not None and line.observed != known:
                out.append(
                    LedgerLine(
                        kind="UNEXPLAINED",
                        ts=line.ts,
                        currency=currency,
                        delta=line.observed - known,
                        balance_after=line.observed,
                        observed=line.observed,
                        reason="balance moved outside the bot",
                    )
                )
            # The game is the source of truth. Whatever the running total
            # said, the number on screen is what the balance actually is -
            # and reading it closes any hole that was open.
            known, stale = line.observed, False

        if known is None or stale or line.delta is None:
            balance = None
        else:
            balance = known + line.delta

        out.append(dataclasses.replace(line, balance_after=balance))

        # Commit the reading unconditionally. An UNEXPLAINED line above has
        # already priced any gap it revealed, so the anchor has to move to it
        # even when this line's own movement is unknown - otherwise the next
        # reading prices the same gap a second time.
        if line.delta is None:
            # Moved by an amount nobody read. The anchor stays where the last
            # reading put it and the chain stops deriving balances, but it
            # keeps reconciling: the next reading prices the whole gap,
            # this purchase's cost included.
            self._known[currency] = known
            self._stale[currency] = True
        else:
            # A known movement counts against the anchor even while stale.
            # Dropping it here is what made a stale chain report a payout as
            # an unexplained GAIN.
            self._known[currency] = None if known is None else known + line.delta
            self._stale[currency] = stale
        return out


# The events the ledger cares about, by the `type` string stored in the
# events table. Anything else is skipped without being rebuilt at all.
_REPLAYABLE: dict[str, type[events.Event]] = {
    "RunEnded": events.RunEnded,
    "Purchased": events.Purchased,
    "PurchaseSkipped": events.PurchaseSkipped,
    "ShoppingStarted": events.ShoppingStarted,
    "ShoppingEnded": events.ShoppingEnded,
    "ShoppingUnavailable": events.ShoppingUnavailable,
    "ControlChanged": events.ControlChanged,
}


def _rebuild(row: dict[str, Any]) -> events.Event | None:
    """Reconstruct a stored row back into the event it came from.

    Best-effort by design. A row written by an older build can be missing
    fields this event now requires, and a ledger line is not worth crashing
    a launch over - such a row is skipped and the history simply starts
    later.
    """
    cls = _REPLAYABLE.get(row["type"])
    if cls is None:
        return None

    fields = {f.name for f in dataclasses.fields(cls)}
    kwargs: dict[str, Any] = {"seq": row["seq"], "ts": row["ts"]}
    if "run_id" in fields and row.get("run_id") is not None:
        kwargs["run_id"] = row["run_id"]
    if "price" in fields and row.get("price") is not None:
        kwargs["price"] = row["price"]
    if "reason" in fields and row.get("reason") is not None:
        kwargs["reason"] = row["reason"]
    kwargs.update({k: v for k, v in (row.get("detail") or {}).items() if k in fields})

    try:
        return cls(**kwargs)
    except TypeError:
        return None


def backfill(conn: sqlite3.Connection) -> int:
    """Replay the events table into the ledger. Returns lines written.

    A ONE-TIME migration, not a repair tool, and it says so by refusing to
    run at all once the ledger holds anything. Derived UNEXPLAINED lines
    have no seq, so the unique index that dedupes replayed source events
    cannot dedupe them; a second unguarded replay would duplicate every one.

    Called from prepare_store BEFORE the prune, so events that are already
    past the retention window still make it into the permanent history.
    """
    if conn.execute("SELECT 1 FROM ledger LIMIT 1").fetchone() is not None:
        return 0

    rows = conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
    writer = LedgerWriter(conn)
    written = 0
    for raw in rows:
        event = _rebuild(_decode_event(raw))
        if event is None:
            continue
        for line in writer.lines_for(event):
            db.insert_ledger(conn, line.as_row())
            written += 1
    return written


def _decode_event(row: Any) -> dict[str, Any]:
    data = dict(row)
    raw = data.get("detail")
    data["detail"] = json.loads(raw) if raw else {}
    return data
