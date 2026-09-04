# Non-Battle Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A durable, append-only log of every non-battle event on this account — purchases, skips, shopping visits, policy changes and run payouts — with a running coin and gem balance beside each line, served on a new `/ledger/` dashboard page.

**Architecture:** A new `ledger` table that pruning never touches, written by the existing store sink so SQLite keeps exactly one writer. A new `ledger.py` splits the work in two: a pure `classify()` that owns the catalog of which events become which lines, and a stateful `LedgerWriter` that owns the running balances and the reconciliation rule that turns an unexplained balance movement into its own row.

**Tech Stack:** Python 3.12, SQLite (stdlib `sqlite3`, WAL), FastAPI, pytest; Next.js 15 static export, React 19, Tailwind, shadcn/base-ui, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-04-non-battle-ledger-design.md`

## Global Constraints

- **In-run events never produce a ledger line.** `Tapped`, `Skipped`, `ScanCompleted`, `ScreenChanged`, `PageChanged`, `Navigated`, `BotError`, `UnknownScreen` all classify to `None`.
- **`delta` semantics are load-bearing.** `0` = provably moved nothing (a skip, a rehearsal). `NULL` = moved by an unknown amount (unreadable price, unreadable payout). Never collapse the two.
- **Unreadable numbers stay `None`, never `0`.** This is the existing rule in `digits.py` — "a refused read, never a wrong price".
- **The store sink stays the database's only writer.** The web layer opens `db.reader()`, which is `mode=ro` at the driver level.
- **`ledger` is never pruned.** `db.prune_events` names `events` explicitly; it must stay that way.
- **Every new `/api` route registers above the `/api/{_path:path}` catch-all** in `web/app.py`. A route below it is unreachable and fails identically to never having been registered.
- **Run tests narrowly.** Only the files touched by the task at hand: `uv run pytest tests/test_x.py -q`. Never `pytest tests/`.
- Python: type hints on every function, `from __future__ import annotations` at the top of each module.
- Commit after every task.

---

### Task 1: Balance readings on `PurchaseSkipped`

The ledger reconciles against balances the bot has read. Today only `Purchased` carries them, and on this account most rows are unaffordable — so the common shopping visit buys nothing and would supply no reading at all. The balance is already in scope at every skip site (`_buy_rows` aborts the whole visit when the header read fails, so `coins` is provably non-None below that guard; `_buy_cards` has the same shape for `gems`).

**Files:**
- Modify: `events.py` (the `PurchaseSkipped` dataclass)
- Modify: `shopping.py` (seven `PurchaseSkipped` publish sites in `_buy_rows` and `_buy_cards`)
- Test: `tests/test_shopping_events.py` (event shape), `tests/test_shopping.py` (a real visit)

**Interfaces:**
- Consumes: nothing.
- Produces: `events.PurchaseSkipped(item: str, reason: str, detail: str = "", coins_before: int | None = None, gems_before: int | None = None)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_shopping_events.py`, which is a pure event-shape file — no fixtures, no visit driving.

```python
def test_a_skipped_workshop_row_reports_the_coin_balance_that_refused_it() -> None:
    """An unaffordable row is a balance reading. Without it the ledger only
    learns a balance on visits that actually bought something, which on this
    account is the rare visit, not the common one."""
    event = events.PurchaseSkipped(
        item="Damage", reason="unaffordable", coins_before=1770
    )

    assert event.coins_before == 1770
    # The currency it did NOT spend stays None rather than being reused -
    # the same rule Purchased already documents.
    assert event.gems_before is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shopping_events.py -q -k coin_balance_that_refused`
Expected: FAIL with `TypeError: PurchaseSkipped.__init__() got an unexpected keyword argument 'coins_before'`

- [ ] **Step 3: Add the fields to the event**

In `events.py`, replace the `PurchaseSkipped` dataclass body:

```python
@dataclass(frozen=True, kw_only=True)
class PurchaseSkipped(Event):
    """One thing not bought, and why.

    `coins_before` and `gems_before` follow the same rule Purchased sets out:
    the currency this row WOULD have spent carries the balance, the other
    stays None rather than being reused for the wrong currency. The ledger
    infers a skip's currency from which of the two is set, so filling both
    would make it report the wrong one.

    They are here because a skip is a balance reading. Most rows on this
    account are unaffordable, so a visit that buys nothing is the common
    case - and without these fields it would tell the ledger nothing about
    where the balances actually stand.
    """

    item: str
    # unaffordable | unreadable | no_match | capped - "disabled" is not in
    # this list on purpose: a disabled row is filtered out of rows_for()
    # before BUY_ROWS ever sees it, so that value is never emitted.
    reason: str
    detail: str = ""
    coins_before: int | None = None
    gems_before: int | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_shopping_events.py -q -k coin_balance_that_refused`
Expected: PASS

- [ ] **Step 5: Pass the balance at every publish site**

In `shopping.py`, `_buy_rows` — all three sites are below the `if coins is None: self._abort(...)` guard, so `coins` is an `int`:

```python
        if match is None:
            self._bus.publish(
                events.PurchaseSkipped(
                    item=rule.name, reason="no_match", coins_before=coins
                )
            )
            self._exhausted.add(rule.name)
            return

        if price is None:
            self._bus.publish(
                events.PurchaseSkipped(
                    item=rule.name, reason="unreadable", detail="price",
                    coins_before=coins,
                )
            )
            self._exhausted.add(rule.name)
            return

        if price > coins:
            self._bus.publish(
                events.PurchaseSkipped(
                    item=rule.name, reason="unaffordable", coins_before=coins
                )
            )
```

In `_buy_cards`, the same for the four sites there, passing `gems_before=gems` instead — `no_match`, `unreadable`, `unaffordable`, and the `capped` / gem-floor skip:

```python
            self._bus.publish(
                events.PurchaseSkipped(
                    item=item, reason="capped", detail="gem floor",
                    gems_before=gems,
                )
            )
```

- [ ] **Step 6: Write the integration assertion**

Add to `tests/test_shopping.py`, directly below `test_a_row_costing_more_than_the_balance_is_skipped_as_unaffordable`. It reuses that test's setup exactly — the `session` and `fake_header` fixtures, `FakeDevice`, `a_policy`, `frame`, and the `Recorder` bus reachable as `session._bus`:

```python
def test_an_unaffordable_skip_reports_the_balance_that_refused_it(
    session, fake_header
) -> None:
    """The skip IS the balance reading. Most rows on this account are
    unaffordable, so a visit that buys nothing is the common one - and
    without this the ledger would learn a balance only on the rare visit
    that bought something."""
    device = FakeDevice()
    policy = a_policy(workshop=(
        ShoppingRule(name="Damage", template="workshop/row_damage.png",
                     category="ATTACK"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    fake_header["coins"] = 10
    session.advance(frame("menu_workshop_attack"), device, policy)

    skips = session._bus.of_type("PurchaseSkipped")
    unaffordable = [s for s in skips if s.reason == "unaffordable"]
    assert unaffordable
    for skip in unaffordable:
        assert skip.coins_before == 10
        # The currency it did not spend stays None rather than being reused,
        # because the ledger reads the currency off whichever field is set.
        assert skip.gems_before is None
```

- [ ] **Step 7: Run the touched test files**

Run: `uv run pytest tests/test_shopping_events.py tests/test_shopping.py tests/test_shopping_loop.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add events.py shopping.py tests/test_shopping_events.py tests/test_shopping.py
git commit -m "feat(events): report the balance on PurchaseSkipped

A skip is a balance reading, and on this account most rows are
unaffordable - so a visit that buys nothing is the common case and
told the ledger nothing about where the balances stand."
```

---

### Task 2: The `ledger` table and its queries

**Files:**
- Modify: `db.py` (`SCHEMA`, plus four new functions)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `db.insert_ledger(conn: sqlite3.Connection, row: dict[str, Any]) -> None`
  - `db.last_balances(conn: sqlite3.Connection) -> dict[str, int | None]` — keys `"coins"` and `"gems"`, always both present
  - `db.count_rehearsals(conn: sqlite3.Connection) -> int`
  - `db.ledger_page(conn, *, limit: int = 50, before: int | None = None, kind: str | None = None, currency: str | None = None, include_rehearsals: bool = False) -> list[dict[str, Any]]` — newest first, `detail` decoded

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def a_line(**overrides: object) -> dict[str, object]:
    line: dict[str, object] = {
        "seq": 1, "ts": 1000.0, "kind": "WORKSHOP_BUY", "item": "Damage",
        "category": "ATTACK", "currency": "coins", "delta": -120,
        "price": 120, "balance_after": 1650, "observed": 1770,
        "dry_run": 0, "run_id": None, "visit": 3, "reason": None,
        "detail": None,
    }
    line.update(overrides)
    return line


def test_a_ledger_page_comes_back_newest_first(tmp_path: Path) -> None:
    conn = make_db(tmp_path)
    for seq in (1, 2, 3):
        db.insert_ledger(conn, a_line(seq=seq, ts=1000.0 + seq))

    page = db.ledger_page(conn)

    assert [line["seq"] for line in page] == [3, 2, 1]


def test_rehearsals_are_excluded_unless_asked_for(tmp_path: Path) -> None:
    """A dry-run line has delta 0 by construction, so showing it by default
    would put rows in a running-balance table that cannot move the balance."""
    conn = make_db(tmp_path)
    db.insert_ledger(conn, a_line(seq=1, dry_run=0))
    db.insert_ledger(conn, a_line(seq=2, dry_run=1, delta=0))

    assert [line["seq"] for line in db.ledger_page(conn)] == [1]
    assert [line["seq"] for line in db.ledger_page(conn, include_rehearsals=True)] == [2, 1]
    assert db.count_rehearsals(conn) == 1


def test_the_last_balance_per_currency_ignores_lines_that_never_had_one(
    tmp_path: Path,
) -> None:
    """An unreadable price leaves balance_after NULL. That is a hole in the
    chain, not a balance of zero, and seeding a writer from it would invent
    a huge bogus UNEXPLAINED on the next real reading."""
    conn = make_db(tmp_path)
    db.insert_ledger(conn, a_line(seq=1, currency="coins", balance_after=1650))
    db.insert_ledger(conn, a_line(seq=2, currency="coins", balance_after=None))
    db.insert_ledger(conn, a_line(seq=3, currency="gems", balance_after=40))

    assert db.last_balances(conn) == {"coins": 1650, "gems": 40}


def test_an_empty_ledger_reports_both_balances_as_unknown(tmp_path: Path) -> None:
    assert db.last_balances(make_db(tmp_path)) == {"coins": None, "gems": None}


def test_ledger_rows_survive_the_event_prune(tmp_path: Path) -> None:
    """The whole reason the ledger is its own table: events age out at 30
    days and an account history that forgets last month is not a history."""
    conn = make_db(tmp_path)
    db.insert_event(conn, a_row(1, ts=0.0))
    db.insert_ledger(conn, a_line(seq=1, ts=0.0))

    removed = db.prune_events(conn, retention_days=30, now=100 * 86400)

    assert removed == 1
    assert len(db.ledger_page(conn)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -q -k ledger or rehearsals or last_balance`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'insert_ledger'`

- [ ] **Step 3: Add the table to `SCHEMA`**

Append to the `SCHEMA` string in `db.py`, after the `events` indexes:

```sql
-- Never pruned, unlike `events`. This is the account's permanent history:
-- what it spent, on what, and what the balances were. `events` answers
-- "what was the bot doing" for 30 days; this answers "what happened to
-- this account" forever.
CREATE TABLE IF NOT EXISTS ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    seq           INTEGER,
    ts            REAL NOT NULL,
    kind          TEXT NOT NULL,
    item          TEXT,
    category      TEXT,
    currency      TEXT,
    delta         INTEGER,
    price         INTEGER,
    balance_after INTEGER,
    observed      INTEGER,
    dry_run       INTEGER NOT NULL DEFAULT 0,
    run_id        INTEGER,
    visit         INTEGER,
    reason        TEXT,
    detail        TEXT
);

-- Partial, because derived UNEXPLAINED lines have no source event and so
-- no seq. It stops an event redelivered across a restart from being
-- counted twice; it deliberately cannot dedupe derived lines, which is why
-- ledger.backfill() guards on the table being empty instead.
CREATE UNIQUE INDEX IF NOT EXISTS ledger_seq_idx  ON ledger(seq) WHERE seq IS NOT NULL;
CREATE INDEX IF NOT EXISTS ledger_ts_idx   ON ledger(ts);
CREATE INDEX IF NOT EXISTS ledger_kind_idx ON ledger(kind);
```

- [ ] **Step 4: Add the four functions**

Add to `db.py`, below `error_log`:

```python
def insert_ledger(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Append one ledger line.

    OR IGNORE, not OR REPLACE: a line already written for this seq is the
    same line, and replacing it would rewrite a balance_after that later
    lines were already computed against.
    """
    conn.execute(
        """INSERT OR IGNORE INTO ledger
               (seq, ts, kind, item, category, currency, delta, price,
                balance_after, observed, dry_run, run_id, visit, reason,
                detail)
           VALUES (:seq, :ts, :kind, :item, :category, :currency, :delta,
                   :price, :balance_after, :observed, :dry_run, :run_id,
                   :visit, :reason, :detail)""",
        row,
    )
    conn.commit()


def last_balances(conn: sqlite3.Connection) -> dict[str, int | None]:
    """The most recent known balance for each currency, or None.

    Skips lines whose balance_after is NULL. That is a hole left by an
    unreadable price, not a balance of zero, and seeding from it would
    invent an enormous UNEXPLAINED line on the next real reading.
    """
    balances: dict[str, int | None] = {"coins": None, "gems": None}
    for currency in balances:
        row = conn.execute(
            """SELECT balance_after FROM ledger
                WHERE currency = ? AND balance_after IS NOT NULL
                ORDER BY id DESC LIMIT 1""",
            (currency,),
        ).fetchone()
        if row is not None:
            balances[currency] = int(row[0])
    return balances


def count_rehearsals(conn: sqlite3.Connection) -> int:
    """How many dry-run lines the ledger holds, for the page's toggle."""
    return int(conn.execute("SELECT COUNT(*) FROM ledger WHERE dry_run = 1").fetchone()[0])


def ledger_page(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    before: int | None = None,
    kind: str | None = None,
    currency: str | None = None,
    include_rehearsals: bool = False,
) -> list[dict[str, Any]]:
    """One page of ledger lines, newest first.

    Ordered and paged by `id`, not `ts`: a derived UNEXPLAINED line shares
    the ts of the event that revealed it, so ts alone cannot order the two,
    and the insertion order is the true one.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if before is not None:
        clauses.append("id < ?")
        params.append(before)
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    if currency is not None:
        clauses.append("currency = ?")
        params.append(currency)
    if not include_rehearsals:
        clauses.append("dry_run = 0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT * FROM ledger {where} ORDER BY id DESC LIMIT ?", params
    ).fetchall()
    return [_decode(row) for row in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat(db): add the ledger table, which pruning never touches"
```

---

### Task 3: `classify()` — the catalog

**Files:**
- Create: `ledger.py`
- Create: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `events.*`.
- Produces:
  - `ledger.COINS = "coins"`, `ledger.GEMS = "gems"`, `ledger.KINDS: tuple[str, ...]`
  - `ledger.LedgerLine` — frozen kw-only dataclass with `kind: str`, `ts: float`, `seq: int | None = None`, `item`, `category`, `currency`, `delta: int | None`, `price: int | None`, `balance_after: int | None`, `observed: int | None`, `dry_run: bool = False`, `run_id`, `visit`, `reason`, `detail: dict[str, Any]`
  - `LedgerLine.as_row() -> dict[str, Any]`
  - `ledger.classify(event: events.Event) -> LedgerLine | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ledger.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ledger.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger'`

- [ ] **Step 3: Write `ledger.py`**

```python
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
from dataclasses import dataclass, field
from typing import Any

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
                delta=0 if currency else None,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ledger.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ledger.py tests/test_ledger.py
git commit -m "feat(ledger): classify events into ledger lines

Pure, and it owns the whole catalog: which events are account history,
which currency they touch, and the delta 0 / delta None distinction
between 'moved nothing' and 'moved an unknown amount'."
```

---

### Task 4: `LedgerWriter` — balances and reconciliation

The bot sees the Workshop and the Cards page and nothing else. Lab slots — which the community's own gem order puts *first* — are invisible to it. So when an observed balance disagrees with the running total, the gap gets its own row rather than being absorbed silently.

**Files:**
- Modify: `ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `ledger.classify`, `ledger.LedgerLine`, `db.last_balances`.
- Produces: `ledger.LedgerWriter(conn: sqlite3.Connection)` with `lines_for(event: events.Event) -> list[LedgerLine]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ledger.py`:

```python
import sqlite3
from pathlib import Path

import db


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ledger.py -q -k writer or balance or reconcile or rehearsal or payout or hole`
Expected: FAIL with `AttributeError: module 'ledger' has no attribute 'LedgerWriter'`

- [ ] **Step 3: Implement `LedgerWriter`**

Add to `ledger.py`. Add `import sqlite3` and `import db` to the imports at the top.

```python
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

        if line.delta is None:
            # Moved by an amount nobody read. Keep the last certain balance
            # so the next reading can price the whole gap, but stop deriving
            # balances from it until then.
            self._stale[currency] = True
        else:
            self._known[currency] = balance if balance is not None else known
            self._stale[currency] = stale
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ledger.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ledger.py tests/test_ledger.py
git commit -m "feat(ledger): reconcile observed balances into UNEXPLAINED lines

The bot can see the Workshop and the Cards page and nothing else, so
a balance that moved behind its back gets an explicit row rather than
letting the running total drift."
```

---

### Task 5: `backfill()`

**Files:**
- Modify: `ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `ledger.LedgerWriter`, `db.insert_ledger`.
- Produces: `ledger.backfill(conn: sqlite3.Connection) -> int` — number of lines written.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ledger.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ledger.py -q -k backfill`
Expected: FAIL with `AttributeError: module 'ledger' has no attribute 'backfill'`

- [ ] **Step 3: Implement `backfill` and its event rebuilder**

Add to `ledger.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ledger.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ledger.py tests/test_ledger.py
git commit -m "feat(ledger): backfill the ledger from stored events, once"
```

---

### Task 6: Wire the store sink

**Files:**
- Modify: `sinks/store.py`
- Test: `tests/test_store_sink.py`

**Interfaces:**
- Consumes: `ledger.LedgerWriter`, `db.insert_ledger`.
- Produces: no new public API. `StoreSink` now writes ledger lines as a side effect of `handle`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_store_sink.py`:

```python
def test_a_purchase_lands_in_both_the_events_table_and_the_ledger(
    tmp_path: Path,
) -> None:
    path = drain(tmp_path, [
        events.Purchased(item="Health", category="DEFENSE", price=75,
                         coins_before=1770, dry_run=False),
    ])

    with db.reader(path) as conn:
        assert len(db.ledger_page(conn)) == 1
        assert db.ledger_page(conn)[0]["kind"] == "WORKSHOP_BUY"
        assert db.last_balances(conn)["coins"] == 1695


def test_a_tap_writes_an_event_row_and_no_ledger_line(tmp_path: Path) -> None:
    """In-run taps are not account history. They are bought with per-run
    cash, which resets."""
    path = drain(tmp_path, [events.Tapped(action="Damage", x=1, y=2, score=0.9)])

    with db.reader(path) as conn:
        assert db.ledger_page(conn) == []


def test_a_failing_ledger_write_still_records_the_event(
    tmp_path: Path, monkeypatch
) -> None:
    """The event history is the more important of the two. A ledger that
    cannot write must lose ledger lines, never event rows - and it must not
    skip the run bookkeeping that follows it either."""
    import ledger

    def boom(self, event):  # noqa: ANN001, ANN201
        raise RuntimeError("ledger is broken")

    monkeypatch.setattr(ledger.LedgerWriter, "lines_for", boom)
    path = drain(tmp_path, [
        events.RunStarted(run_id=1),
        events.RunEnded(run_id=1, duration=10.0, wave=5, coins=100, tier=1),
        events.Tapped(action="Damage", x=1, y=2, score=0.9),
    ])

    with db.reader(path) as conn:
        stored = conn.execute("SELECT type FROM events ORDER BY seq").fetchall()
        assert [row[0] for row in stored] == ["RunStarted", "RunEnded", "Tapped"]
        # _run_id was still cleared after RunEnded, so the trailing tap is
        # not misattributed to the run that already finished.
        orphan = conn.execute(
            "SELECT run_id FROM events WHERE type = 'Tapped'"
        ).fetchone()
        assert orphan[0] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store_sink.py -q -k ledger`
Expected: FAIL — `db.ledger_page` returns `[]` for the purchase

- [ ] **Step 3: Wire the writer in**

In `sinks/store.py`, add `import ledger` to the imports. Add the field in `__init__`:

```python
        self._ledger: ledger.LedgerWriter | None = None
```

In `_consume`, build it once the connection exists:

```python
    def _consume(self) -> None:
        self._conn = db.connect(self.path)
        # After connect, because the writer seeds its running balances from
        # the ledger table - a restart that started from zero would read the
        # next real balance as an enormous unexplained gain.
        self._ledger = ledger.LedgerWriter(self._conn)
        try:
            super()._consume()
        finally:
            self._conn.close()
            self._conn = None
            self._ledger = None
```

At the end of `handle`, after `db.insert_event(conn, to_row(event, self._run_id))` and **before** the `RunEnded` cleanup:

```python
        db.insert_event(conn, to_row(event, self._run_id))

        # Guarded separately from the event write above. QueueSink._consume
        # already catches everything, so a raise here could not kill the
        # thread - but it would skip the _run_id cleanup below, and every
        # orphaned event after a run ended would then be filed under the run
        # that already finished. The event history is the more important of
        # the two tables; the ledger must never cost it.
        if self._ledger is not None:
            try:
                for line in self._ledger.lines_for(event):
                    db.insert_ledger(conn, line.as_row())
            except Exception:  # noqa: BLE001
                logger.exception("could not write the ledger line for %s", event.type)

        if isinstance(event, events.RunEnded):
            # Orphaned events after a run ends belong to no run, not to the ended run.
            self._run_id = None
```

Add a module logger near the imports if `store.py` does not already have one:

```python
logger = logging.getLogger("tower_bot.sinks.store")
```

(with `import logging` alongside the other imports).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_store_sink.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sinks/store.py tests/test_store_sink.py
git commit -m "feat(store): write ledger lines alongside event rows

Same sink, same thread, same connection - so SQLite still has exactly
one writer and the web layer's read-only connections stay safe."
```

---

### Task 7: Backfill on launch, before the prune

**Files:**
- Modify: `tower_bot.py` (`prepare_store`, around line 983)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ledger.backfill`.
- Produces: no signature change. `prepare_store` still returns `(max_seq, max_run_id)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`, beside the existing `prepare_store` tests:

```python
def test_prepare_store_backfills_the_ledger_before_pruning(tmp_path) -> None:
    """Ordering is the whole point. An event already past the retention
    window is deleted by the prune, so if backfill ran second the permanent
    history would be born already missing everything older than 30 days."""
    path = tmp_path / "bot.db"
    conn = db.connect(path)
    conn.execute(
        """INSERT INTO events (seq, run_id, ts, type, screen, action, reason,
                               score, price, wallet, detail)
           VALUES (1, NULL, 0.0, 'Purchased', NULL, NULL, NULL, NULL, 75, NULL,
                   '{"item": "Health", "category": "DEFENSE",
                     "coins_before": 1770, "gems_before": null,
                     "dry_run": false}')"""
    )
    conn.commit()
    conn.close()

    tower_bot.prepare_store(path, retention_days=30)

    with db.reader(path) as reader:
        # The event itself aged out...
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        # ...but the ledger kept it, which is why the ledger exists.
        page = db.ledger_page(reader)
        assert len(page) == 1
        assert page[0]["kind"] == "WORKSHOP_BUY"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -q -k backfills_the_ledger`
Expected: FAIL — the ledger page is empty

- [ ] **Step 3: Call backfill in `prepare_store`**

Add `import ledger` to `tower_bot.py`'s imports. In `prepare_store`, between `close_abandoned_runs` and `prune_events`:

```python
        written = ledger.backfill(conn)
        if written:
            logger.info("Backfilled %d ledger line(s) from stored events", written)
        removed = db.prune_events(conn, retention_days)
```

And extend the docstring, after the paragraph about reading `max_seq` before pruning:

```
    Backfills the ledger BEFORE pruning too, and for a related reason: the
    ledger is the permanent account history and `events` is not, so an event
    already past the retention window has to reach the ledger on its way out.
    Backfill refuses to run once the ledger holds anything, so this is a
    one-time migration despite being called on every launch.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q -k prepare_store`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tower_bot.py tests/test_cli.py
git commit -m "feat(cli): backfill the ledger at launch, before the prune"
```

---

### Task 8: `GET /api/ledger`

**Files:**
- Modify: `web/app.py` (insert after the `/api/errors` route, ~line 602)
- Test: `tests/test_web_api.py`, `tests/test_lifecycle_api.py`

**Interfaces:**
- Consumes: `db.ledger_page`, `db.last_balances`, `db.count_rehearsals`.
- Produces: `GET /api/ledger` → `{"lines": [...], "balances": {"coins": int | null, "gems": int | null}, "rehearsals": int, "next": int | null}`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_api.py`:

```python
def a_ledger_line(seq: int, **overrides: object) -> dict[str, object]:
    line: dict[str, object] = {
        "seq": seq, "ts": 1000.0 + seq, "kind": "WORKSHOP_BUY", "item": "Health",
        "category": "DEFENSE", "currency": "coins", "delta": -75, "price": 75,
        "balance_after": 1695, "observed": 1770, "dry_run": 0, "run_id": None,
        "visit": 1, "reason": None, "detail": None,
    }
    line.update(overrides)
    return line


def test_the_ledger_route_returns_lines_newest_first_with_balances(harness) -> None:
    client, _, _, _, db_path, _ = harness
    conn = db.connect(db_path)
    db.insert_ledger(conn, a_ledger_line(1))
    db.insert_ledger(conn, a_ledger_line(2, balance_after=1620, observed=1695))
    conn.close()

    body = client.get("/api/ledger").json()

    assert [line["seq"] for line in body["lines"]] == [2, 1]
    assert body["balances"] == {"coins": 1620, "gems": None}
    assert body["rehearsals"] == 0


def test_the_ledger_route_hides_rehearsals_but_counts_them(harness) -> None:
    client, _, _, _, db_path, _ = harness
    conn = db.connect(db_path)
    db.insert_ledger(conn, a_ledger_line(1))
    db.insert_ledger(conn, a_ledger_line(2, dry_run=1, delta=0))
    conn.close()

    hidden = client.get("/api/ledger").json()
    shown = client.get("/api/ledger?include_rehearsals=true").json()

    assert [line["seq"] for line in hidden["lines"]] == [1]
    assert hidden["rehearsals"] == 1
    assert [line["seq"] for line in shown["lines"]] == [2, 1]


def test_the_ledger_route_pages_with_a_cursor(harness) -> None:
    client, _, _, _, db_path, _ = harness
    conn = db.connect(db_path)
    for seq in range(1, 4):
        db.insert_ledger(conn, a_ledger_line(seq))
    conn.close()

    first = client.get("/api/ledger?limit=2").json()
    assert len(first["lines"]) == 2
    assert first["next"] == first["lines"][-1]["id"]

    second = client.get(f"/api/ledger?limit=2&before={first['next']}").json()
    assert len(second["lines"]) == 1
    assert second["next"] is None


def test_the_ledger_route_answers_no_store_with_an_empty_ledger(harness) -> None:
    """--no-store is a supported mode. An empty account is the honest
    answer; a 500 is not."""
    client, state, sse, bus, _, unknown_dir = harness
    app = create_app(state=state, sse=sse, bus=bus, db_path=None,
                     unknown_dir=unknown_dir)

    body = TestClient(app).get("/api/ledger").json()

    assert body == {
        "lines": [], "balances": {"coins": None, "gems": None},
        "rehearsals": 0, "next": None,
    }
```

Add to `tests/test_lifecycle_api.py`, beside the existing catch-all test:

```python
def test_the_ledger_route_is_not_shadowed_by_the_api_catch_all(harness) -> None:
    client = harness[0]

    assert client.get("/api/ledger").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_api.py -q -k ledger`
Expected: FAIL with 404 — the route does not exist

- [ ] **Step 3: Add the route**

In `web/app.py`, immediately after the `/api/errors` route and **above** the `/api/{_path:path}` catch-all:

```python
    # Above the catch-all below, like every other /api route. A route
    # registered after it is unreachable and 404s exactly as if it had never
    # been wired, which is a far more confusing failure than normal
    # shadowing - see that route's comment.
    @app.get("/api/ledger")
    def ledger_lines(
        limit: int = 50,
        before: int | None = None,
        kind: str | None = None,
        currency: str | None = None,
        include_rehearsals: bool = False,
    ) -> dict:
        # --no-store: there is no file to read, so an empty account history
        # is the honest answer, the same as /api/runs and /api/errors.
        if db_path is None:
            return {
                "lines": [],
                "balances": {"coins": None, "gems": None},
                "rehearsals": 0,
                "next": None,
            }
        capped = max(1, min(limit, MAX_LEDGER_PER_PAGE))
        with db.reader(db_path) as conn:
            lines = db.ledger_page(
                conn,
                limit=capped,
                before=before,
                kind=kind,
                currency=currency,
                include_rehearsals=include_rehearsals,
            )
            return {
                "lines": lines,
                "balances": db.last_balances(conn),
                "rehearsals": db.count_rehearsals(conn),
                # Only a full page can have more behind it. A short page is
                # the end, and claiming otherwise costs the client a request
                # that returns nothing.
                "next": lines[-1]["id"] if len(lines) == capped else None,
            }
```

Add the cap beside the existing `MAX_RUNS_PER_PAGE` constant near the top of `web/app.py`:

```python
MAX_LEDGER_PER_PAGE = 200
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_api.py tests/test_lifecycle_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_web_api.py tests/test_lifecycle_api.py
git commit -m "feat(web): serve the ledger at /api/ledger"
```

---

### Task 9: Teach the live feed the shopping events

The `/ledger/` page refreshes when a ledger-worthy event arrives on the shared stream, so the browser has to recognise those types. They are currently missing from both the union and the formatter, which is also why they render in the live feed today as a bare chip with no body.

**Files:**
- Modify: `web/ui/lib/types.ts` (the `BotEvent` union)
- Modify: `web/ui/lib/format.ts` (`splitEvent`)
- Modify: `web/ui/components/EventFeed.tsx` (the `CHIP` map)
- Test: `web/ui/lib/format.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `BotEvent` gains the `Purchased`, `PurchaseSkipped`, `ShoppingStarted`, `ShoppingEnded`, `ShoppingUnavailable` and `PageChanged` members; `splitEvent` returns kinds `BUY`, `NOBUY`, `SHOP` and `PAGE` for them.

- [ ] **Step 1: Write the failing test**

Add to `web/ui/lib/format.test.ts`:

```ts
it("describes a purchase with what it cost and what it spent", () => {
  const line = splitEvent({
    seq: 1, ts: 0, type: "Purchased", item: "Health", category: "DEFENSE",
    price: 75, coins_before: 1770, gems_before: null, dry_run: false,
  });

  expect(line.kind).toBe("BUY");
  expect(line.body).toContain("Health");
  expect(line.body).toContain("75");
});

it("marks a rehearsal so it cannot be read as a real purchase", () => {
  const line = splitEvent({
    seq: 1, ts: 0, type: "Purchased", item: "Health", category: "DEFENSE",
    price: 75, coins_before: 1770, gems_before: null, dry_run: true,
  });

  expect(line.body).toContain("rehearsal");
});

it("describes a skipped purchase with its reason", () => {
  const line = splitEvent({
    seq: 1, ts: 0, type: "PurchaseSkipped", item: "Damage",
    reason: "unaffordable", detail: "", coins_before: 1770, gems_before: null,
  });

  expect(line.kind).toBe("NOBUY");
  expect(line.body).toContain("unaffordable");
});

it("describes the end of a shopping visit", () => {
  const line = splitEvent({
    seq: 1, ts: 0, type: "ShoppingEnded", visit: 3, bought: 2, spent: 95,
    aborted: false, reason: "",
  });

  expect(line.kind).toBe("SHOP");
  expect(line.body).toContain("2");
  expect(line.body).toContain("95");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `web/ui`): `npm test -- format`
Expected: FAIL — TypeScript rejects the unknown `type` values, and `splitEvent` returns no `BUY` kind

- [ ] **Step 3: Extend the union**

In `web/ui/lib/types.ts`, add to the `BotEvent` union:

```ts
  | (EventBase & { type: "PageChanged"; prev_page: string; curr_page: string; confidence: number })
  | (EventBase & { type: "ShoppingStarted"; visit: number; dry_run: boolean })
  | (EventBase & { type: "ShoppingUnavailable"; reason: string })
  | (EventBase & { type: "Purchased"; item: string; category: string; price: number | null; coins_before: number | null; gems_before: number | null; dry_run: boolean })
  | (EventBase & { type: "PurchaseSkipped"; item: string; reason: string; detail: string; coins_before: number | null; gems_before: number | null })
  | (EventBase & { type: "ShoppingEnded"; visit: number; bought: number; spent: number; aborted: boolean; reason: string })
```

- [ ] **Step 4: Add the formatter cases**

In `web/ui/lib/format.ts`, add to `splitEvent`'s switch, before the default:

```ts
    case "Purchased":
      return {
        kind: "BUY",
        // "rehearsal" spelled out rather than a dry_run flag: this line is
        // read at a glance on a second monitor, and a purchase that did not
        // happen must not look like one that did.
        body: `${event.item} ${money(event.price)}${event.dry_run ? " (rehearsal)" : ""}`,
      };
    case "PurchaseSkipped":
      return {
        kind: "NOBUY",
        body: `${event.item} reason=${event.reason}${event.detail ? " " + event.detail : ""}`,
      };
    case "ShoppingStarted":
      return { kind: "SHOP", body: `visit #${event.visit}${event.dry_run ? " (rehearsal)" : ""}` };
    case "ShoppingEnded":
      return {
        kind: "SHOP",
        body: `visit #${event.visit} ${event.aborted ? "aborted" : "done"} bought=${event.bought} spent=${event.spent}${event.reason ? " " + event.reason : ""}`,
      };
    case "ShoppingUnavailable":
      return { kind: "SHOP", body: event.reason };
    case "PageChanged":
      return {
        kind: "PAGE",
        body: `${event.prev_page} -> ${event.curr_page} (${event.confidence.toFixed(3)})`,
      };
```

- [ ] **Step 5: Give the new kinds a chip colour**

In `web/ui/components/EventFeed.tsx`, add to `CHIP`:

```ts
  BUY: "bg-chart-2/15 text-chart-2",
  NOBUY: "bg-muted text-muted-foreground",
  SHOP: "bg-chart-2/15 text-chart-2",
  PAGE: "bg-chart-1/15 text-chart-1",
```

- [ ] **Step 6: Run tests to verify they pass**

Run (from `web/ui`): `npm test -- format && npx tsc --noEmit`
Expected: PASS, no type errors

- [ ] **Step 7: Commit**

```bash
git add web/ui/lib/types.ts web/ui/lib/format.ts web/ui/components/EventFeed.tsx web/ui/lib/format.test.ts
git commit -m "fix(web): give the shopping events a line in the feed

They have been published since shopping shipped and rendered as a bare
type chip with no body, because neither the union nor splitEvent knew
they existed."
```

---

### Task 10: The `/ledger/` page

**Files:**
- Create: `web/ui/app/ledger/page.tsx`
- Create: `web/ui/app/ledger/page.test.tsx`
- Modify: `web/ui/lib/types.ts` (add `LedgerLine`, `LedgerPayload`)
- Modify: `web/ui/lib/api.ts` (add `fetchLedger`)

**Interfaces:**
- Consumes: `getJson`, `SectionCard`, `PageHeader`, `useEventStream`.
- Produces:
  - `types.LedgerLine` — `{ id: number; seq: number | null; ts: number; kind: string; item: string | null; category: string | null; currency: string | null; delta: number | null; price: number | null; balance_after: number | null; observed: number | null; dry_run: number; run_id: number | null; visit: number | null; reason: string | null; detail: Record<string, unknown> }`
  - `types.LedgerPayload` — `{ lines: LedgerLine[]; balances: { coins: number | null; gems: number | null }; rehearsals: number; next: number | null }`
  - `api.fetchLedger(opts?: { includeRehearsals?: boolean; before?: number }) => Promise<LedgerPayload>`

- [ ] **Step 1: Write the failing test**

Create `web/ui/app/ledger/page.test.tsx`:

```tsx
// fireEvent, not @testing-library/user-event: user-event is not a
// dependency of this project and every other test here uses fireEvent.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe as group, expect, it, vi } from "vitest";
import LedgerPage from "./page";

const { fetchLedger } = vi.hoisted(() => ({ fetchLedger: vi.fn() }));

vi.mock("@/lib/api", () => ({ fetchLedger }));
vi.mock("@/lib/useEventStream", () => ({ useEventStream: () => ({ events: [], connected: true }) }));

function payload(overrides = {}) {
  return {
    lines: [], balances: { coins: null, gems: null }, rehearsals: 0, next: null,
    ...overrides,
  };
}

const A_PURCHASE = {
  id: 1, seq: 1, ts: 0, kind: "WORKSHOP_BUY", item: "Health",
  category: "DEFENSE", currency: "coins", delta: -75, price: 75,
  balance_after: 1695, observed: 1770, dry_run: 0, run_id: null, visit: 1,
  reason: null, detail: {},
};

group("LedgerPage", () => {
  it("shows an explicit empty state with nothing recorded", async () => {
    fetchLedger.mockResolvedValue(payload());
    render(<LedgerPage />);

    await screen.findByText("Nothing recorded yet.");
  });

  it("does not report an unreachable server as an empty account", async () => {
    fetchLedger.mockRejectedValue(new Error("/api/ledger -> 503"));
    render(<LedgerPage />);

    await screen.findByText(/could not load/i);
    expect(screen.queryByText("Nothing recorded yet.")).toBeNull();
  });

  it("lists a purchase with its delta and running balance", async () => {
    fetchLedger.mockResolvedValue(
      payload({ lines: [A_PURCHASE], balances: { coins: 1695, gems: 40 } }),
    );
    render(<LedgerPage />);

    await screen.findByText("Health");
    expect(screen.getByText("-75")).toBeDefined();
    expect(screen.getByText("1,695")).toBeDefined();
  });

  it("flags an unexplained line as the account moving outside the bot", async () => {
    fetchLedger.mockResolvedValue(payload({
      lines: [{
        ...A_PURCHASE, id: 2, seq: null, kind: "UNEXPLAINED", item: null,
        currency: "gems", delta: -140, price: null, balance_after: 40,
        observed: 40,
      }],
    }));
    render(<LedgerPage />);

    await screen.findByText("UNEXPLAINED");
    expect(screen.getByText(/outside the bot/i)).toBeDefined();
  });

  it("hides rehearsals until asked, and says how many there are", async () => {
    fetchLedger.mockResolvedValue(payload({ lines: [A_PURCHASE], rehearsals: 3 }));
    render(<LedgerPage />);

    const toggle = await screen.findByRole("button", { name: /show rehearsals \(3\)/i });
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(fetchLedger).toHaveBeenLastCalledWith({ includeRehearsals: true }),
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `web/ui`): `npm test -- ledger`
Expected: FAIL — `Cannot find module './page'`

- [ ] **Step 3: Add the types and the fetcher**

In `web/ui/lib/types.ts`:

```ts
/** A row from the `ledger` table - the account's permanent non-battle
 *  history. Distinct from StoredEvent, which is the 30-day event log. */
export interface LedgerLine {
  id: number;
  /** The source event's bus seq, or null for a derived UNEXPLAINED line. */
  seq: number | null;
  ts: number;
  kind: string;
  item: string | null;
  category: string | null;
  currency: string | null;
  /** What actually moved. 0 means "provably nothing" (a skip, a rehearsal);
   *  null means "an unknown amount" (an unreadable price). */
  delta: number | null;
  price: number | null;
  balance_after: number | null;
  observed: number | null;
  dry_run: number;
  run_id: number | null;
  visit: number | null;
  reason: string | null;
  detail: Record<string, unknown>;
}

export interface LedgerPayload {
  lines: LedgerLine[];
  balances: { coins: number | null; gems: number | null };
  rehearsals: number;
  next: number | null;
}
```

In `web/ui/lib/api.ts`, beside the other readers:

```ts
export const fetchLedger = (opts: { includeRehearsals?: boolean; before?: number } = {}) => {
  const params = new URLSearchParams();
  if (opts.includeRehearsals) params.set("include_rehearsals", "true");
  if (opts.before !== undefined) params.set("before", String(opts.before));
  const query = params.toString();
  return getJson<LedgerPayload>(`/api/ledger${query ? `?${query}` : ""}`);
};
```

Add `LedgerPayload` to that file's `import type { … } from "./types"` list.

- [ ] **Step 4: Write the page**

Create `web/ui/app/ledger/page.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { SectionCard } from "@/components/ui/section-card";
import { fetchLedger } from "@/lib/api";
import { clock } from "@/lib/format";
import type { LedgerPayload } from "@/lib/types";
import { useEventStream } from "@/lib/useEventStream";
import { cn } from "@/lib/utils";

/** The event types that change the ledger. The page refetches when one
 *  arrives rather than deriving a line in the browser: the catalog and the
 *  balance arithmetic live in ledger.py, and a second implementation here
 *  would be a second thing to get wrong. */
const LEDGER_EVENTS = new Set([
  "Purchased", "PurchaseSkipped", "ShoppingStarted", "ShoppingEnded",
  "ShoppingUnavailable", "ControlChanged", "RunEnded",
]);

const KIND_TONE: Record<string, string> = {
  UNEXPLAINED: "bg-warn-surface text-warn",
  WORKSHOP_BUY: "bg-chart-2/15 text-chart-2",
  CARD_BUY: "bg-chart-2/15 text-chart-2",
  RUN_PAYOUT: "bg-live-surface text-live",
  BUY_SKIPPED: "bg-muted text-muted-foreground",
};

const num = (value: number | null) =>
  value === null ? "—" : value.toLocaleString("en-US");

export default function LedgerPage() {
  const [data, setData] = useState<LedgerPayload | null>(null);
  // A failed fetch must never fall into the same empty state as a healthy
  // account - reporting an unreachable server as "nothing recorded" would
  // be the same defect the Errors page calls out.
  const [failed, setFailed] = useState<string | null>(null);
  const [rehearsals, setRehearsals] = useState(false);
  const { events } = useEventStream();

  const load = useCallback(() => {
    fetchLedger(rehearsals ? { includeRehearsals: true } : {})
      .then((payload) => { setData(payload); setFailed(null); })
      .catch((e: Error) => setFailed(e.message));
  }, [rehearsals]);

  useEffect(load, [load]);

  // Refetch on the last event only, not the whole array: the feed grows on
  // every scan and re-running this for each one would hammer the route.
  const latest = events[events.length - 1];
  useEffect(() => {
    if (latest && LEDGER_EVENTS.has(latest.type)) load();
  }, [latest, load]);

  const lines = data?.lines ?? [];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Ledger"
        meta="everything outside a run"
        action={
          data ? (
            <span className="font-mono text-xs text-muted-foreground">
              {num(data.balances.coins)} coins · {num(data.balances.gems)} gems
            </span>
          ) : null
        }
      />

      <SectionCard
        title="History"
        tone={failed ? "warn" : undefined}
        action={
          data?.rehearsals ? (
            <button
              type="button"
              onClick={() => setRehearsals((on) => !on)}
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
            >
              {rehearsals
                ? `hide rehearsals (${data.rehearsals})`
                : `show rehearsals (${data.rehearsals})`}
            </button>
          ) : null
        }
      >
        {failed ? (
          <div className="rounded-md border border-warn bg-warn-surface p-3">
            <p className="text-sm text-warn">Could not load the ledger.</p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">{failed}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              This is not the same as an empty account — the dashboard could not
              reach the bot.
            </p>
          </div>
        ) : lines.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-[0.12em] text-faint-foreground">
                  <th className="py-1 pr-3 font-medium">Time</th>
                  <th className="py-1 pr-3 font-medium">Kind</th>
                  <th className="py-1 pr-3 font-medium">Item</th>
                  <th className="py-1 pr-3 text-right font-medium">Δ</th>
                  <th className="py-1 pr-3 text-right font-medium">Balance</th>
                  <th className="py-1 font-medium">Note</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((line) => (
                  <tr key={line.id} className="border-t">
                    <td className="py-1.5 pr-3 font-mono text-[11px] text-faint-foreground">
                      {clock(line.ts)}
                    </td>
                    <td className="py-1.5 pr-3">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 font-mono text-[10px]",
                          KIND_TONE[line.kind] ?? "bg-muted text-muted-foreground",
                        )}
                      >
                        {line.kind}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3">
                      {line.item ?? "—"}
                      {line.dry_run ? (
                        <span className="ml-1.5 text-xs text-muted-foreground">
                          rehearsal
                        </span>
                      ) : null}
                    </td>
                    <td
                      className={cn(
                        "py-1.5 pr-3 text-right font-mono",
                        line.delta !== null && line.delta > 0 && "text-live",
                        line.delta !== null && line.delta < 0 && "text-muted-foreground",
                      )}
                    >
                      {line.delta === null ? "?" : line.delta > 0 ? `+${line.delta}` : line.delta}
                    </td>
                    <td className="py-1.5 pr-3 text-right font-mono text-muted-foreground">
                      {num(line.balance_after)}
                      {line.currency ? (
                        <span className="ml-1 text-[10px] text-faint-foreground">
                          {line.currency}
                        </span>
                      ) : null}
                    </td>
                    <td className="py-1.5 text-xs text-muted-foreground">
                      {line.kind === "UNEXPLAINED"
                        ? "balance moved outside the bot"
                        : line.reason ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>
        )}
      </SectionCard>
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `web/ui`): `npm test -- ledger && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add web/ui/app/ledger web/ui/lib/types.ts web/ui/lib/api.ts
git commit -m "feat(web): add the ledger page"
```

---

### Task 11: Link it, build it, ship the static bundle

**Files:**
- Modify: `web/ui/components/Sidebar.tsx`
- Modify: `web/static/**` (generated by `npm run build`)
- Modify: `README.md`

**Interfaces:**
- Consumes: the `/ledger/` route from Task 10.
- Produces: nothing new.

- [ ] **Step 1: Add the rail item**

In `web/ui/components/Sidebar.tsx`, add `Receipt` to the `lucide-react` import and add the item to the **Watch** group, after Errors:

```tsx
      { href: "/ledger/", label: "Ledger", icon: Receipt },
```

- [ ] **Step 2: Run the frontend suite and the type check**

Run (from `web/ui`): `npm test && npx tsc --noEmit && npm run lint`
Expected: PASS

- [ ] **Step 3: Build the static bundle**

Run (from `web/ui`): `npm run build`
Expected: the build succeeds and `web/static/ledger/index.html` now exists

- [ ] **Step 4: Verify the served page**

Run (from the repo root): `uv run pytest tests/test_web_build.py -q`
Expected: PASS

- [ ] **Step 5: Document it**

In `README.md`, add this subsection under **Where the data goes**:

```markdown
### The ledger

The `events` table is pruned at `EVENT_RETENTION_DAYS` (30). The `ledger`
table is not — it is the account's permanent history of everything that
happens *outside* a run: workshop and card purchases, the rows it skipped
and why, shopping visits, policy changes, and each run's coin payout. In-run
upgrades never appear: they are bought with per-run cash that resets, which
is not account history. The dashboard serves it at **/ledger/**, newest
first, with the running coin and gem balance after every line.

Rehearsals (`shopping.enabled` without `armed`) are recorded but hidden
behind a toggle. They carry the price they would have paid and a delta of
zero, so they can never move a balance.

**Unexplained lines.** The bot can see the Workshop and the Cards page and
nothing else — not labs, lab slots, modules, relics, ultimate weapons, the
guild, the shop, or ad rewards. The community's own gem order puts lab slots
first, so the largest gem sink on this account is invisible to it. Rather
than let the running balance drift, the ledger compares every balance it
reads against what its own lines predict and writes the difference as an
`UNEXPLAINED` line: "−140 gems, balance moved outside the bot".

Two limits worth knowing. A gain and a loss of equal size between two
readings cancel out and produce no line — what is reported is the *net*
movement between observations. And the bot only reads a balance during a
shopping visit, so an `UNEXPLAINED` line is dated to the visit that
*revealed* the gap, not to when the spend actually happened.
```

- [ ] **Step 6: Commit**

```bash
git add web/ui/components/Sidebar.tsx web/static README.md
git commit -m "feat(web): link the ledger in the rail and ship the build"
```

---

## Done when

- `uv run pytest tests/test_ledger.py tests/test_db.py tests/test_store_sink.py tests/test_shopping_events.py tests/test_cli.py tests/test_web_api.py tests/test_lifecycle_api.py -q` passes.
- `npm test && npx tsc --noEmit` passes in `web/ui`.
- `/ledger/` lists purchases, skips, visits, policy changes and run payouts with a running balance, hides rehearsals behind a counted toggle, and flags `UNEXPLAINED` lines.
- A ledger line survives `db.prune_events`.
