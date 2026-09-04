# The Tower bot — buying in the menus, and a guide that says why

Status: designed, not yet implemented
Date: 2026-09-03

Builds on `2026-09-02-bot-lifecycle-and-strategy-design.md`, whose `Strategy`
model and dashboard this design extends.

## 1. Context

The bot buys upgrades inside a run and nothing else. That is not an accident
of scope — it is an invariant, written down in `navigate.py`:

> Nothing here spends permanent resources: in-run upgrades are bought with
> per-run cash that resets, and coins are only ever earned.

Every safety property the bot has today rests on that sentence. Actions fire
on `IN_RUN` and nowhere else, that rule is hardcoded rather than
configurable, and auto-navigation taps exactly two buttons. The worst a
miscalibrated template can currently do is waste a run.

This design deliberately breaks that invariant. Workshop upgrades cost coins
and cards cost gems; both are permanent, both are earned slowly, and neither
can be refunded. So most of what follows is not about tapping buttons — it
is about what has to be true before the bot is allowed to tap one.

There is a second, quieter gap. Someone already cut and committed the hard
part of menu navigation: `config.NAV_TARGETS` (the workshop, cards, missions
and battle tabs), `config.PAGE_ANCHORS` (an anchor per menu page),
`config.NAV_DISMISS` (the first-visit popup chain), four page fixtures, and
`tests/test_nav_templates.py` proving each anchor matches its own page at
≥0.9 and every other page below 0.75. None of it is referenced by a single
line of bot code. The templates are calibrated and the machinery to use them
does not exist.

### What the device actually shows

Measured on the live emulator on 2026-09-03, not inferred from the wiki.

A workshop row is three boxes: a label, a **stat value**, and a **price**.

| Row | Value box | Price |
|---|---|---|
| Damage | `3` | 30 |
| Attack Speed | `1.00` | 30 |
| Critical Chance | `1.00%` | 50 |
| Critical Factor | `x1.20` | 50 |
| Health | `5` | 30 |
| Health Regen | `0.00/sec` | 30 |

There is **no level number anywhere on the row**. The level exists only
behind a per-row detail panel. Any design phrased as "buy Def Abs to level
50" — which is how the whole community writes strategy — has to be
translated into something the screen actually shows.

The account is also earlier than every guide assumes. The Utility tab holds
exactly one tile, `Unlock Cash Bonuses — 40`. Defense holds Health, Health
Regen, and `Unlock Defense Upgrades — 75`. Attack holds the four rows above
and `Unlock Range Upgrades — 50`. Cash/Wave, Coins/Wave, Def Abs, Def%,
Thorns and Coins/Kill — the six upgrades the community names first — **do
not exist on this account yet**. They are behind 165 coins of unlock tiles,
against a balance of 1.77K.

That balance is also the problem. The header reads `1.77K`, and the README
already admits the consequence:

> **Known gap: no suffix glyphs.** `K`, `M`, `B`, `.` and `,` are not in any
> atlas on disk.

The bot cannot read its own coin balance. Every purchase gate in this design
is `coins >= price`, so closing that gap is not a follow-up — it is a
prerequisite, and it is in scope here.

## 2. Goals

- Buy workshop upgrades between runs, in a priority order the dashboard can
  edit, spending down to whatever is left unaffordable.
- Buy cards with gems under a hard floor and a per-visit cap, off by default.
- Navigate to the Workshop and Cards pages and back, using the templates
  already calibrated for it.
- Read coins and gems off the menu header, which means finishing the atlas.
- Ship a rehearsal mode that reads, decides and reports without tapping, and
  default to it — the first calibration error must cost nothing.
- A Guide page in the dashboard carrying the researched community strategy,
  with the reasoning next to the knob it justifies.
- Keep every invariant the current design earned: one settings snapshot per
  scan, `publish()` never blocks, all-or-nothing validation, one shutdown
  path, and no fixed coordinates anywhere.

### Non-goals

- **Equipping or levelling cards.** Levelling happens by duplicate draws,
  which the bot gets for free by buying. Equipping means recognising 31 card
  faces and reasoning about slots, and the community's own advice is that
  slot choice depends on build and tier — a judgement the bot cannot make.
  It buys; you equip.
- **Card slots, labs, modules, relics, the shop tab.** The community gem
  order puts lab slots *above* cards, and the bot cannot see labs at all. A
  bot that spends gems on the second-best thing while blind to the best one
  is worse than a bot that spends none. It never taps these.
- **Ultimate Weapon selection.** Irreversible, escalating in cost, and the
  single most-cited account-damaging mistake in the community's own
  footguns guide. Never automated.
- **Reading upgrade levels.** Requires opening a per-row detail panel, which
  covers the page and blinds the next scan. Not worth it — see section 4.
- **Missions.** `MISSIONS` and `MISSIONS_RETURN` templates exist and stay
  unused. Claiming mission rewards is a separate errand with its own reward
  modals; nothing here depends on it.
- **Authentication.** Still none, and now worse — see section 11.

## 3. The buying rule

**Buy the highest-priority affordable row, repeat until no enabled row on
the page is affordable.**

That is the whole policy. Row order is priority, exactly as `Strategy.actions`
already works, and for the same stated reason: a separate priority field
would be a second way to say what the tuple already says.

Three alternatives were considered and rejected.

**Per-row price ceiling** (`buy Damage while it costs ≤ 500`). Price rises
monotonically with level, so a ceiling is a level target you can actually
read. Rejected as the default because it is a second number per row to keep
correct, and the spend-down rule already self-limits: buying a row raises its
price, so the top row eventually prices itself out and spending falls to the
next. Worth keeping in mind as a one-field addition if section 12's caveat
proves real.

**Stat-value target** (`buy Damage until value ≥ 20`). The most literal
reading of community advice, and the most vision surface: five value formats
across six rows (`3`, `1.00`, `1.00%`, `x1.20`, `0.00/sec`), each needing its
own glyphs and parser. A misread value here spends coins, and the existing
`digits` reader is all-or-nothing precisely because a plausible wrong number
is the dangerous failure.

**Bot-counted purchases as a proxy level.** Exact and free, until a human
buys something by hand — after which it is silently wrong forever, with no
way to reseed, because the level is never displayed. It survives as
telemetry (section 8), never as a gate.

Unlock tiles (`Unlock Cash Bonuses`, `Unlock Defense Upgrades`, `Unlock Range
Upgrades`) are ordinary rows with ordinary templates. They sit at the top of
the shipped default order because on this account they are the correct first
purchase, and because everything the community recommends is behind them.

## 4. Control flow

A shopping visit is a multi-step errand against a single-shot scan loop. Two
ways to fit those together, and only one of them keeps the architecture.

**Rejected: a blocking routine.** `run_once` calls into a function that
captures and taps in a tight loop until the errand is done. Faster — a visit
is seconds instead of a minute — but it stalls the scan loop, so Pause stops
responding, the frame stream freezes, and the whole errand lands in the
event feed as one opaque entry after the fact.

**Chosen: one step per scan.** `ShoppingSession` is a state machine that
advances by exactly one step per `run_once`. One capture, one decision, at
most one tap — the same shape as every other thing the loop does. Pause
works because it works everywhere; the dashboard watches the errand happen;
a wedged step is visible rather than silent. At a 2s interval a full visit is
roughly a minute of wall clock, between runs, which costs nothing.

```
MAIN_MENU confirmed
        │
        ▼
  OPEN_WORKSHOP ──▶ TAB(first category) ──▶ BUY* ──▶ TAB(next category) ──▶ BUY*
                                                                              │
                    IDLE ◀── RETURN ◀── BUY_CARDS ◀── OPEN_CARDS ◀────────────┘
```

Category order is derived from the priority list, not hardcoded — see below.

`BUY*` is not one step. It is: match every enabled row for this category
against the frame, read each matched row's price, and if any is affordable,
tap the **highest-priority** one — first in strategy order, not cheapest —
and stay in `BUY*`. When nothing on the page is affordable, advance. A page
whose anchor does not match is a bail-out, not a retry.

### Category order, and what greedy means across tabs

Section 3's rule is global — "the highest-priority affordable row" — but rows
live on three tabs and only one tab is readable at a time. Taken literally,
honouring global order means re-checking every tab after every purchase,
which thrashes tabs and burns the tap budget on navigation.

So the visit order of the categories is **derived from the priority list**:
the order in which each category first appears among the enabled rows. With
the section 7 defaults that is Utility, then Defense, then Attack. Within a
category the rule is exactly section 3's, spending down until nothing on that
page is affordable, and then the visit moves on and does not come back.

The tradeoff is explicit: coins left unspent when Utility runs out of
affordable rows cannot flow back to Utility later in the same visit. Since
every category is visited every visit and visits happen between every run,
that only ever defers a purchase by one run. It buys a deterministic,
tap-cheap, testable single pass, and the alternative — a global re-scan loop
— is not worth a minute of tab-switching per run.

### Three collisions with existing code

These are the parts that are not new code but changed code, and they are
where this design can break something that currently works.

**`Navigator` must be suppressed while a session is live.** It taps `BATTLE`
on `MAIN_MENU` after a 3s cooldown. Left alone it starts a run in the middle
of an errand. The session takes precedence: `maybe_navigate` is not called
while `ShoppingSession` is anything but `IDLE`.

**Workshop and Cards are `UNKNOWN` to the screen tracker.** `SCREEN_ANCHORS`
holds three entries and none of them is a menu page — `config.py` says that
separation is deliberate, because `classify()` does `ScreenState(winner)` and
would raise on a name the enum lacks. That stays true. But it means every
scan inside a visit currently confirms `UNKNOWN`, and a confirmed `UNKNOWN`
writes a snapshot. Unknown-screen snapshotting is therefore suppressed while
a session is live, or every errand fills `unknown/` with pictures of the
workshop and evicts the genuine mysteries the directory exists to hold.

**Run tracking must not see a visit as a run boundary.** A run closes on the
first confirmed state that is not `IN_RUN`, and `MAIN_MENU` closes it as
abandoned. A visit starts *from* `MAIN_MENU`, after that transition has
already happened, so the ordering is safe as written — but it is an ordering
the tests must pin, not a property to assume.

### Bailing out

A session that cannot make progress must not strand the bot in a menu.
Three exits, all publishing `ShoppingEnded(aborted=True)` and tapping the
Battle tab on the way out:

- `max_taps_per_visit` exceeded (default 40).
- The visible page is not the page the state machine expects, twice running.
- Any step raising.

The Battle tab is matched, not assumed. If even that fails, the session goes
`IDLE` and lets the normal `UNKNOWN` handling take over — which snapshots the
frame, which is exactly the trail you want for a bot lost in a menu.

## 5. Vision

### Page classification

New `pages.py`, mirroring `screens.py`: `classify_page(frame, cache)` over
`config.PAGE_ANCHORS`, returning a page name, a confidence and every anchor's
score. Separate from `screens.classify` for the reason `config.py` already
gives, and only consulted while a session is live — outside one, a menu page
staying `UNKNOWN` is correct behaviour.

The separation is already measured: 0.9+ on the right page, ≤0.592 on every
wrong one, per `test_nav_templates.py`.

### New templates

Cut at 1080x2400 from live captures, committed with their fixtures:

- `workshop/tab_attack.png`, `tab_defense.png`, `tab_utility.png` — the
  category tabs above the bottom nav bar.
- `workshop/row_damage.png`, `row_attack_speed.png`, `row_critical_chance.png`,
  `row_critical_factor.png`, `row_health.png`, `row_health_regen.png` — row
  labels. **New files, not reused from the in-run crops**: the workshop
  renders these words at a different size on a different background, and
  `matchTemplate` is not scale invariant.
- `workshop/unlock_cash_bonuses.png`, `unlock_defense_upgrades.png`,
  `unlock_range_upgrades.png`.
- `cards/buy_x1.png`, `cards/buy_x10.png`.

### Regions

Prices come from a region relative to the row's **own matched label**, not
from a page anchor — the same rule `PRICE_REGION` follows in-run, and for the
same reason: each row has its own box, and one calibration that lands on all
of them beats six that drift.

Coins and gems come from regions relative to the page anchor. The header is
identical across Workshop, Cards and the main menu, so one pair of regions
serves all three.

### Where to tap

Unresolved, and deliberately so. In-run, the label is itself a button that
opens an info panel — `config.buy_point()` exists entirely because of that
trap, and it derives the buy square from `PRICE_REGION` rather than measuring
it twice. The workshop row may well be a single tile where any tap buys, but
that is a guess, and a wrong guess here opens a panel that blinds the next
scan.

So it is calibrated, once, with a deliberate armed tap on a 30-coin row,
with the user watching. Not before. Until then `buy_point` for workshop rows
is derived the same way as in-run and marked as unverified in the code.

### Closing the atlas gap

A new `header` size class under `templates/atlas/header/`, harvested with the
existing `build_atlas.py --size-class header` and labelled with
`tools/label_glyphs.py`, plus the missing suffix glyphs `K`, `M`, `B` and `.`
in whichever classes can be harvested for them.

This is not incidental. `1.77K` fails to read today, `read()` returns `None`
on any partial recognition by design, and a `None` coin balance means no
purchase can ever be approved. **The feature does not function until this is
done**, which makes it the first implementation step, not the last.

There is no brightness fallback available here either. In-run, a failed
digit read falls back to brightness for that action. On a menu page there is
no equivalent — an unaffordable workshop row is not reliably dimmed, and
guessing is exactly the failure mode that costs coins. A failed coin read
therefore skips the whole visit and publishes why.

## 6. Strategy schema

Additive. Every field has a default that reproduces today's behaviour, so
existing `strategies/*.json` load unchanged and a bot with no `shopping` key
behaves exactly as it does now.

```json
"shopping": {
  "enabled": false,
  "armed": false,
  "visit_every_n_runs": 1,
  "max_taps_per_visit": 40,
  "workshop": [
    {
      "name": "Unlock Cash Bonuses",
      "template": "workshop/unlock_cash_bonuses.png",
      "category": "UTILITY",
      "enabled": true,
      "threshold": 0.9,
      "brightness_ratio": 0.75
    }
  ],
  "cards": {
    "enabled": false,
    "gem_floor": 40,
    "max_per_visit": 2,
    "batch": "x1"
  }
}
```

`enabled` and `armed` are two switches on purpose. `enabled=true, armed=false`
is the rehearsal: navigate, read, decide, publish every decision, tap
nothing. `armed` is the only thing standing between a calibration error and
your coins, so it is its own field rather than a mode buried in another one.

Validation follows `ActionRule` exactly — a types table shared between
`__post_init__` and the field-naming helper, unique names, `category` against
an enum, `threshold` exclusive at zero, `brightness_ratio` inclusive at zero,
and the existing containment check on template paths. `gem_floor` is
non-negative; `max_per_visit` and `max_taps_per_visit` are at least 1;
`batch` is `x1` or `x10`.

`PATCHABLE_FIELDS` gains `shopping`, handled the way `actions` is — parsed by
one function used by both `from_dict` and `merged`, so "unknown field" means
one thing on every endpoint.

## 7. Shipped default order

The default strategy ships a workshop order taken from the community
consensus in section 10, adjusted to what this account can actually see:

1. `Unlock Cash Bonuses` (Utility, 40)
2. `Unlock Defense Upgrades` (Defense, 75)
3. `Unlock Range Upgrades` (Attack, 50)
4. `Health` (Defense)
5. `Health Regen` (Defense)
6. `Damage` (Attack)
7. `Attack Speed` (Attack)
8. `Critical Chance` (Attack, disabled)
9. `Critical Factor` (Attack, disabled)

The two crit rows ship `enabled: false` because the community is unanimous
that they cost more and scale slower than everything above them early on.
They are present so the row exists to switch on, which is the same reason
`enabled` exists at all.

Rows the account cannot see yet — Cash/Wave, Coins/Wave, Def Abs, Def%,
Thorns, Coins/Kill — are **not** in the default file. A row whose template
was never cut against a real frame is a row that matches nothing or matches
everything. They get added when the unlocks reveal them and the templates are
cut against a live capture, which is a documented follow-up, not a stub.

## 8. Events and persistence

| Event | Carries |
|---|---|
| `PageChanged` | `prev`, `curr`, `confidence` |
| `ShoppingStarted` | `visit`, `coins`, `gems`, `dry_run` |
| `Purchased` | `item`, `category`, `price`, `coins_before`, `dry_run` |
| `PurchaseSkipped` | `item`, `reason` (`unaffordable` / `unreadable` / `no_match` / `disabled`) |
| `ShoppingEnded` | `visit`, `bought`, `spent`, `aborted`, `reason` |

All are state changes, so all are stored — none of them fires per scan the
way `ScanCompleted` does. Every new field rides the events table's JSON
`detail` blob, so **there is no migration**. That is the property the
existing schema was built for and this is the first feature to actually use
it.

The bot-counted purchase tally from section 3 lives here: it is derived by
counting `Purchased` events, which means it is reconstructable from the log,
survives restarts, and is honestly labelled as "what this bot bought" rather
than pretending to be your level.

## 9. Dashboard

**Strategy page** gains a Shopping section: the enable and arm switches, the
visit cadence, the workshop row editor (reorder, toggle, per-row threshold
and brightness, same controls the action rows already have), and the cards
policy. Each row carries a one-line hint drawn from the Guide content, so the
reasoning sits next to the knob it justifies rather than a click away.

The arm switch is visually distinct from every other control on the page and
states what it does in plain words. It is the only control in this dashboard
that spends something you cannot get back.

**Live page** shows an active visit inline — the page the session is on, what
it has bought, what it has spent — because a minute-long errand that shows up
only as scattered feed entries is hard to follow while it happens.

**Guide page**, new in the sidebar, section 10.

## 10. The Guide page

The content, sourced. This is the deliverable the request actually leads
with — the automation is how the guidance gets acted on.

**Workshop, in community order.** Economy first: Cash/Wave and Coins/Wave
early, switching to Cash Bonus and Coins/Kill around wave 100, because coins
per kill dominates the economy long-term while coins per wave has fewer
multipliers to grow into. Defense next: Def Abs to roughly 50 workshop
levels, then Def%, Health up a level or two early, Thorns held 1% above a
fraction breakpoint (6%, 11%, 21%, 26%). Attack last: Damage and Attack
Speed, with crit deferred because it costs more and scales slower.

**Gems, in community order.** Lab slots first. Then three cards — Attack
Speed, Enemy Balance, Coins — with the slots to equip them. Then the third
lab slot, then Health and Cash cards, then the fourth lab slot. Only after
that do Crit Coin, Wave Skip and Extra Orbs come in. Do not chase specific
epics; relics are generally worse value than modules and card levels.

**Card mechanics.** 20 gems a card, 200 for ten. Draw odds are 80% common,
17% rare, 3% epic, with rare rising to 97% once every common is maxed. A card
maxes at 7 stars and takes 80 copies — 1,600 gems. The community target is
buying enough every two weeks to finish the 80-card event.

**Where this account is.** Named explicitly, with the three unlock tiles and
their prices, because the general guidance above is describing upgrades this
account cannot see yet, and a guide that does not say so is misleading.

**What the bot will not do, and why.** Lab slots outrank cards and the bot
cannot see labs. UW selection is irreversible. Card equipping depends on
build and tier. Each stated as a limitation with its reason, so the page
reads as an honest account of the division of labour rather than a feature
list.

Every claim links its source. The pages used:
`tower-hub.com/wiki/guide/beginner-guide`, `/gem-guide`,
`/coin-guide-basics`, `/footguns`, and `/card/cards`.

The page is static content in the Next.js app, rebuilt into `web/static/` the
same way every other page is. No new API surface.

## 11. Security

Unchanged in mechanism, worse in consequence, and the README's warning needs
to grow a sentence.

The dashboard has no authentication and binds loopback for good reasons
already documented: it streams the device, serves the full event history, and
can start, stop and shut down the bot and rewrite strategy files on disk.

This design adds a new one. Anything that can reach the port can now flip
`armed`, reorder the buy list, and drop `gem_floor` to zero — which is to say
it can spend a resource that took weeks to earn, in a game account, with no
undo. That is a different class of harm from "pause the bot", and it is the
first control here whose damage survives the process exiting.

Loopback is doing more work than it was. It still is not authentication.

## 12. Risks

**Greedy pours coins into the top row.** With order as the only policy, the
top row absorbs everything until its own escalating price outruns the
balance. That self-limits, but a cheap top row goes deep before anything
below it is touched. Accepted deliberately — it is the rule that was asked
for, it needs no per-row tuning, and section 3's price ceiling is a one-field
addition if the run log says it is wrong.

**The buy point is unverified.** Section 5. Mitigated by calibrating it with
one watched tap before anything is armed.

**Menu layout is dynamic.** Rows appear as unlocks are bought, so a row's
position moves. This is why everything is template-matched and why fixed
coordinates appear nowhere — but it also means the fixtures committed today
stop describing the account the moment the bot buys an unlock. Fixtures get
recaptured when the layout changes, and the golden tests are what make that
visible instead of silent.

**Game updates move the UI.** Same exposure the existing templates already
carry, and the same detection: golden tests fail loudly.

**A visit overlapping a run.** Guarded by the `IDLE` check on navigation and
pinned by tests, but this is the interaction most likely to produce a
surprising bug, because it involves three stateful collaborators — the
tracker, the run tracker and the session — reading the same frame.

## 13. Testing

- **Golden templates.** Every new template matches its own page at ≥0.9 and
  every other committed page below 0.75, in the style of
  `test_nav_templates.py`. Fixtures: the four live captures from 2026-09-03.
- **Page classifier.** Separation across all four pages, plus `UNKNOWN` below
  threshold.
- **`ShoppingSession` as a pure state machine.** Driven by stubbed frames and
  a fake device, no ADB. Every transition, every bail-out, the tap cap, the
  unexpected-page exit, and the dry-run path proving zero taps reach the
  device.
- **Category order.** Derived from the priority list, not hardcoded:
  reordering rows so an Attack row leads must make Attack the first tab
  visited.
- **Interaction tests.** Navigation suppressed during a visit; unknown
  snapshots suppressed during a visit; run boundaries unaffected by a visit.
- **Strategy validation.** Every new field's type and range, unknown-field
  rejection, and defaults reproducing current behaviour on a file with no
  `shopping` key.
- **Digits.** `1.77K` reads as 1770 once the suffix glyphs land, and a
  missing glyph still returns `None` rather than a plausible wrong number.
- **Brightness, finally measured.** The Cards page currently shows `x1` lit
  and `x10` greyed at 40 gems — the unaffordable state the README admits was
  never captured. Both get cut, the ratio measured in both states, and the
  0.75 default either confirmed or corrected with a number behind it.
- **Web.** The Guide page renders; the Shopping section round-trips through
  the strategy API; the arm switch requires an explicit confirm.

## 14. Implementation order

1. Atlas: `header` size class and the suffix glyphs. Nothing else works
   first, and it independently closes a documented README gap.
2. `pages.py` and the page classifier, over templates that already exist.
3. Templates and fixtures for the rows, tabs and card buttons.
4. Schema: `shopping` on `Strategy`, with validation and defaults.
5. `ShoppingSession`, dry-run only, wired into `run_once` with the three
   suppressions from section 4.
6. Events, storage and the Live page display.
7. Guide page and the Strategy page section.
8. Calibrate the buy point with one watched tap; arm.

Steps 1–7 cannot spend a coin. That ordering is the point: everything is
built, observed and reviewed against a real device before `armed` is ever
true.
