# Non-battle ledger

A durable, append-only log of everything that happens to this account
*outside* a run, with a running coin and gem balance beside it.

The dashboard already answers "what is the bot doing right now" (Live),
"how did that run go" (Runs, Stats) and "what broke" (Errors). Nothing
answers "what has this account spent, and on what". Shopping made that
question real: it is the first feature that spends a resource the game
does not hand back, and its purchases currently land in the same 30-day
`events` table as 43,000 scans a day, indistinguishable in the feed and
deleted a month later.

## Scope

**In:** every non-battle event that moves a permanent resource or explains
why one did or did not move.

**Out:** everything in-run. `Tapped`, `Skipped`, `ScanCompleted`,
`ScreenChanged`, `PageChanged` and `Navigated` never produce a ledger line.
In-run upgrades are bought with per-run cash that resets, so they are not
account history; `wallet` on `Tapped`/`ScanCompleted` is that per-run cash
and is a different currency from `coins`.

A battle contributes exactly one thing: its payout. `RunEnded.coins` is
read off the game-over modal's *Coins* caption — coins earned that run, not
a running total — which makes it a clean credit line.

`BotError` and `UnknownScreen` are also out. They have their own page, and
the shopping-relevant ones already arrive as `VISIT_END(aborted, reason)`.

## The catalog

Every line has a `kind`. Two money columns, kept distinct on purpose:

* `delta` — what **actually** moved. Never a hypothetical.
* `price` — what it cost, or would have cost.

Separating them is what makes dry-run rehearsals safe to log: a rehearsal
records the price it would have paid and a `delta` of zero, so it can never
move a balance no matter how it is queried.

| kind | source | currency | delta | price |
|---|---|---|---|---|
| `RUN_PAYOUT` | `RunEnded` | coins | `+coins`, NULL if unreadable | — |
| `WORKSHOP_BUY` | `Purchased`, category ≠ CARDS | coins | `−price`; 0 when `dry_run` | price |
| `CARD_BUY` | `Purchased`, category = CARDS | gems | `−price`; 0 when `dry_run` | price |
| `BUY_SKIPPED` | `PurchaseSkipped` | coins or gems | — | — |
| `VISIT_START` | `ShoppingStarted` | — | — | — |
| `VISIT_END` | `ShoppingEnded` | — | — | — |
| `SHOP_UNAVAILABLE` | `ShoppingUnavailable` | — | — | — |
| `POLICY_CHANGED` | `ControlChanged` | — | — | — |
| `UNEXPLAINED` | derived (see below) | coins or gems | the gap | — |

`currency` is set whenever a line **moves or observes** a balance, even
where `delta` is NULL. A `BUY_SKIPPED` for an unaffordable workshop row
moves nothing but does report the coin balance that made it unaffordable,
and that reading is what the reconciliation rule below runs against.

`POLICY_CHANGED` covers strategy activation too: `POST
/api/strategies/{name}/activate` publishes `ControlChanged`, so switching
profiles is already on the bus and needs no new event.

### One upstream change: balances on `PurchaseSkipped`

`Purchased` carries `coins_before` and `gems_before`. `PurchaseSkipped`
does not, and it must, or the ledger will almost never reconcile: on this
account most rows are unaffordable, so the common visit buys nothing at all
and would supply no reading whatsoever.

The balance is already in hand at every skip site. `_buy_rows` reads
`coins` from the header and aborts the whole visit when it is None, so
every `PurchaseSkipped` it publishes — `no_match`, `unreadable`,
`unaffordable` — provably has a good coin balance in scope; `_buy_cards`
has the identical shape for gems. The fields exist to be filled and are
simply not being passed.

So: add `coins_before: int | None = None` and `gems_before: int | None =
None` to `PurchaseSkipped`, with the same rule `Purchased` already
documents — the currency this skip *would* have spent gets the balance, the
other stays None rather than being reused for the wrong currency. Defaulted
and untyped in the schema, they land in the JSON `detail` blob, so this
needs no migration.

This is the only change to an existing event in the whole design.

**Reserved, with no code behind them:** `LAB`, `CARD_SLOT`, `MODULE`,
`RELIC`, `UW`, `MANUAL`. Named so the parts of the economy the bot cannot
see yet have a home when they arrive. Nothing emits them today.

### Why the reserved kinds matter

The bot can see the Workshop and the Cards page. It cannot see labs, lab
slots, card slots, modules, relics, Ultimate Weapons, the guild, the shop,
daily missions or ad rewards — some out of scope, some with no template or
classifier at all. The community's own gem order puts **lab slots first,
above cards**, so the single largest gem sink on this account is invisible
to the bot by construction.

A ledger that recorded only what the bot did would therefore be quietly,
permanently wrong about gems. The reconciliation rule below is the answer.

## Reconciliation

Balance readings do **not** get their own rows. Every `Purchased` carries
`coins_before` / `gems_before` and every `PurchaseSkipped` will once the
change above lands; a row per reading would bury the log in observations.
Each line carries the reading in an `observed` column instead, and a
reading is promoted to a row only when it contradicts the arithmetic:

> `coins_before` on a `WORKSHOP_BUY` is the balance *before* that buy. When
> it does not match the previous line's `balance_after` for that currency,
> an `UNEXPLAINED` line for the difference is written immediately before it.
> Then `balance_after = coins_before − price`.

Coins and gems keep independent chains; the rule runs per currency.

This is what keeps the log honest about the invisible economy. Buying a lab
slot with gems produces `UNEXPLAINED −140 gems` at the next reading, rather
than a balance that silently drifts.

It also self-heals the unreadable-price case. A real purchase whose price
could not be read gets `price = NULL` and `delta = NULL`; the hole stays a
hole until the next reading turns it into an explicit `UNEXPLAINED` line.
An unknown is never rendered as a zero.

Two known limits, stated rather than hidden.

A gain and a loss of equal size between two readings cancel out and produce
no line. The ledger reports the *net* unexplained movement between
observations, which is the most any sampled balance can honestly claim.

And readings only arrive during a shopping visit, because that is the only
time the bot is on a page whose header it can read. Reconciliation
resolution is therefore per-visit, not continuous: with `shopping.enabled`
false, or between visits, the ledger records payouts and policy changes but
learns nothing new about the balances. An `UNEXPLAINED` line is dated to
the visit that *revealed* the gap, not to whenever the spend actually
happened, and the page says so rather than implying a precision it does not
have.

## Storage

One new table in `db.py`'s `SCHEMA`. It is **never pruned**.

```sql
CREATE TABLE IF NOT EXISTS ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    seq           INTEGER,          -- source event's bus seq; NULL when derived
    ts            REAL NOT NULL,
    kind          TEXT NOT NULL,
    item          TEXT,
    category      TEXT,
    currency      TEXT,             -- 'coins' | 'gems' | NULL
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

CREATE UNIQUE INDEX IF NOT EXISTS ledger_seq_idx ON ledger(seq) WHERE seq IS NOT NULL;
CREATE INDEX IF NOT EXISTS ledger_ts_idx   ON ledger(ts);
CREATE INDEX IF NOT EXISTS ledger_kind_idx ON ledger(kind);
```

Three choices earn their place:

* **`id` is its own key rather than `seq`.** Derived `UNEXPLAINED` lines
  have no source event. They are inserted immediately before the line that
  revealed them, so `ORDER BY id` is the canonical order with no tiebreak.
* **The partial unique index on `seq`** makes every write idempotent via
  `INSERT OR IGNORE`. That is what lets backfill run on every launch and
  makes any replay safe.
* **`AUTOINCREMENT`** means ids are never reused, so a delete cannot
  silently reorder the chain.

`balance_after` is computed at write time, not at read time. A page of rows
is therefore complete on its own — rendering the last 50 lines needs no
history before them.

## Components

### `events.py` and `shopping.py`

`PurchaseSkipped` gains `coins_before` and `gems_before`, both
`int | None = None`, and the four publish sites in `_buy_rows` /
`_buy_cards` pass the balance already in scope. Nothing else changes.

### `ledger.py` (new)

Split by statefulness, so the judgment is testable without a database.

* `classify(event) -> LedgerLine | None` — **pure**. Owns the catalog table
  above and nothing else. Fills `kind`, `item`, `category`, `currency`,
  `delta`, `price`, `observed`, `dry_run`, `run_id`, `visit`, `reason`.
  Leaves `balance_after` unset. Returns `None` for every event not in the
  catalog.
* `LedgerWriter` — holds `{currency: balance}`, seeded from the table when
  constructed so a restart does not invent a bogus `UNEXPLAINED` from a
  balance of zero. `lines_for(event) -> list[LedgerLine]` calls `classify`,
  prepends an `UNEXPLAINED` when `observed` contradicts the running
  balance, sets `balance_after`, updates its state, and returns the list in
  insertion order.
* `backfill(conn) -> int` — replays the existing `events` table in `seq`
  order through a fresh `LedgerWriter`. Idempotent by the unique index.

### `db.py`

Gains `insert_ledger(conn, line)`, `ledger_page(conn, *, limit, before,
kind, currency, include_rehearsals)` and `ledger_totals(conn)`.
`prune_events` is unchanged — it names `events` explicitly — and gains a
test pinning that ledger rows survive it.

### `sinks/store.py`

Gains a handful of lines: build a `LedgerWriter` after connecting in
`_consume`, and after `db.insert_event`, loop `self._ledger.lines_for(event)`
into `db.insert_ledger` inside the guard described under Error handling.
The store sink remains the database's only writer,
which is what preserves the one-writer/many-readers invariant the web
layer's read-only connections depend on.

A second `LedgerSink` was considered and rejected for exactly that reason:
it would put a second writer on the same file, and giving it its own file
would make joining `runs` impossible.

### `tower_bot.py`

`prepare_store` calls `ledger.backfill(conn)` **before** `db.prune_events`.

Ordering is load-bearing and mirrors a discipline the function already has.
It reads `max_seq` before pruning so a handed-out seq is never reissued;
backfill has the identical shape for a different reason — replay first, or
events already past the 30-day retention are deleted before the ledger ever
sees them. `prepare_store`'s docstring already names it "the only place
that legitimately holds the writable connection outside the store sink's
own thread", which is what makes it the right home.

### `web/app.py`

```
GET /api/ledger?limit=&before=&kind=&currency=&include_rehearsals=
→ { "lines": [...], "balances": {"coins": 1770, "gems": 40}, "next": <id|null> }
```

Cursor pagination on `id`, newest first. `--no-store` (`db_path is None`)
returns `{"lines": [], "balances": {"coins": null, "gems": null}, "next":
null}`, matching how `/api/runs` and `/api/errors` treat that mode.

**Registered above the `/api/{_path:path}` catch-all.** That route swallows
every write verb under `/api` and Starlette matches in registration order,
so a route added below it is unreachable in a way that looks identical to
never having been registered. `web/app.py` carries a comment saying so and
`tests/test_lifecycle_api.py` pins it.

### `web/ui`

* `app/ledger/page.tsx` — a new page built from the existing `PageHeader`,
  `SectionCard` and `ui/table` primitives.
* `components/Sidebar.tsx` — a `Ledger` item in the **Watch** group, below
  Errors, using lucide's `Receipt`.
* `lib/api.ts` — `fetchLedger`.
* `lib/types.ts` — a `LedgerLine` interface, plus the missing event shapes
  below.

Columns: time, kind chip, item, Δ (signed and coloured), balance, note.

Filters: kind chips, a coins/gems/all toggle, and a
`rehearsals hidden (N)` toggle. Dry-run lines default to **hidden**: they
are `delta = 0` by construction, and rows that do not move the balance do
not belong in a running-balance table by default. The count in the label
keeps them discoverable rather than secret.

`UNEXPLAINED` rows render in the warn tone. They are the signal that the
account moved outside the bot, which is the most actionable thing this page
can say.

The empty state distinguishes **"could not load"** from **"nothing
recorded"**. `app/errors/page.tsx` calls conflating those two "the most
dangerous thing this page could possibly say"; a ledger that reports an
unreachable server as an empty account has the same defect.

### Live refresh

The page subscribes to the shared event stream (`useEventStream`) and
refetches when a ledger-worthy event *type* arrives. It never derives lines
in the browser: the catalog and the balance arithmetic stay in `ledger.py`,
one implementation, one place to be wrong.

That requires closing an existing gap. `lib/types.ts`'s `BotEvent` union and
`lib/format.ts`'s `splitEvent` have no cases for `Purchased`,
`PurchaseSkipped`, `ShoppingStarted`, `ShoppingEnded`, `ShoppingUnavailable`
or `PageChanged`, so today those render in the live feed as a bare type chip
with no body. Adding them is a prerequisite for the refresh trigger and
repairs the feed as a side effect.

## Error handling

* A `classify` that does not recognise an event returns `None`. Unknown is
  never guessed at.
* `LedgerWriter.lines_for` must not be able to kill the store sink's
  consumer thread. It is wrapped the way the rest of `handle` is: a raise is
  logged and the event row is still written. A broken ledger loses ledger
  lines, never the event history.
* Unreadable numbers stay `NULL`, never `0`. This is the same rule the
  digit reader already enforces — "a refused read, never a wrong price" —
  and the reconciliation rule is what eventually accounts for the gap.
* `--no-store` is a supported mode, not an error. Empty is the honest
  answer.

## Testing

New `tests/test_ledger.py`, alongside the existing per-module test files:

* `classify` — one case per catalog row, plus the exclusions: every in-run
  event type returns `None`.
* Dry-run: `delta == 0`, `price` preserved, balance unmoved across a
  rehearsal visit.
* Reconciliation: a matching reading emits no `UNEXPLAINED`; a short
  reading emits one with the right sign and currency; coins and gems
  reconcile independently; an unreadable price leaves a hole that the next
  reading resolves.
* `LedgerWriter` seeded from a non-empty table does not emit a spurious
  `UNEXPLAINED` on its first event after a restart.
* `backfill` is idempotent — running it twice leaves the row count
  unchanged.

Additions to existing files:

* `tests/test_shopping_events.py` — every `PurchaseSkipped` a visit
  publishes carries the balance for the currency it would have spent, and
  None for the other.
* `tests/test_db.py` — ledger rows survive `prune_events`.
* `tests/test_store_sink.py` — a `Purchased` produces both an event row and
  a ledger row; a raising ledger writer still writes the event row.
* `tests/test_web_api.py` — `/api/ledger` shape, pagination, filters, and
  the `--no-store` answer.
* `tests/test_lifecycle_api.py` — the new route is not shadowed by the
  catch-all.
* Vitest — `app/ledger/page.test.tsx` for the three states (loaded, empty,
  failed) and the rehearsal toggle; `lib/format.test.ts` for the new
  `splitEvent` cases.

## Out of scope

* Manual entry of purchases the bot cannot see. The `MANUAL` kind is
  reserved for it; no form, no write route, no UI today.
* Attributing an `UNEXPLAINED` line after the fact ("that −140 was a lab
  slot").
* Any aggregate or summary view. The page is a running-balance statement;
  `ledger_totals` exists for the header's current balances only.
* Reading any new part of the game. No new templates, no new classifier.
