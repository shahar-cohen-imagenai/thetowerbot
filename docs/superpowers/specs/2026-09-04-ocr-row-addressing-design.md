# The Tower bot — addressing upgrades by name instead of by template

Status: designed, not yet implemented
Date: 2026-09-04

Builds on `2026-09-03-menu-shopping-design.md`, whose buying rule, safety
gates and `Shopping` schema this design keeps and whose vision layer it
replaces.

## 1. Context

Every upgrade the bot can buy is identified by a cropped PNG. `Strategy`
carries a `template` on every action and every shopping row, and
`Strategy.validated()` refuses to load a profile whose templates are not on
disk. That rule is deliberate and it is also a ceiling: **the bot can only
ever buy an upgrade someone has already photographed.**

Today that is 14 files — four in-run upgrades and ten workshop rows —
and it is the reason a community strategy cannot be expressed as a
strategy file. The Tier 1 "Turtle" build is built almost entirely on
upgrades with no template: Defense Absolute, Thorns, Cash/Wave, Cash Bonus,
Coins/Kill Bonus, Coins/Wave, Defense %. None can be cut, because none is
visible: all seven sit behind the three unlock tiles this account has not
bought. So the templates cannot be made until the account progresses, and
the account progresses by buying things the bot cannot name.

Cutting seven more PNGs would not fix this. It would move the ceiling by
seven and leave the shape of the problem intact — every future tier's build
naming upgrades nobody has photographed yet.

This design removes the ceiling: an upgrade is addressed by **its name, read
off the screen**, and a strategy file names upgrades the way a guide does.

### What the spike proved

Measured on 2026-09-04 against the four committed fixtures, at
1080x2400. Scripts were throwaway; nothing was committed and
`pyproject.toml` was untouched (`uv run --with rapidocr-onnxruntime`).

**The existing glyph atlas cannot read these labels.** `digits.glyph_spans`
projects over every row of a region at once, so stacked text merges:

| Label | Spans found | Letters expected |
|---|---|---|
| `Damage` | 6 | 6 |
| `Health` | 6 | 6 |
| `Attack Speed` | **2** | 11 |
| `Critical Chance` | **4** | 14 |
| `Critical Factor` | **4** | 14 |
| `Health Regen` | **4** | 11 |

Kerning was not the problem — single-line labels split cleanly at threshold
170. **Line wrapping is.** `Attack Speed`, `Critical Chance`, `Critical
Factor` and `Health Regen` all wrap onto two lines, in the workshop *and* in
the in-run panel. Reaching them via the atlas would need line segmentation,
per-line column projection, and a hand-labelled ~52-glyph alphabet per size
class — with no complete reference class to auto-label against, since
`label_glyphs.py` scores candidates against an already-complete class and no
class contains letters.

**RapidOCR reads all of it, with no templates at all.** Whole-frame OCR,
zero anchoring:

| Page | Read | Confidence |
|---|---|---|
| attack | Damage, Attack Speed, Critical Chance, Critical Factor, Unlock Range Upgrades | 0.993–0.999 |
| defense | Health, Health Regen, Unlock Defense Upgrades | 0.984–0.999 |
| utility | Unlock Cash Bonuses | 0.968 |
| in-run | Damage, Attack Speed, Critical Chance, Critical Factor | 0.992–1.000 |

It also returned every price (`30`, `50`, `75`, `40`; in-run `$10 $5 $4`),
the wallet and gems (`1.77K`, `40`), and the page heading
(`ATTACKUPGRADES` / `DEFENSEUPGRADES` / `UTILITYUPGRADES`). Whole-frame cost
was **213–333 ms** against a 2.0 s scan interval.

Four findings constrain the design:

1. **OCR returns boxes, not rows.** `Critical`(133,763) and
   `Chance`(133,813) are separate detections, and `Critical`/`Factor` sit on
   the same page. Joining in reading order yields
   `"Critical Critical Chance Factor"`.
2. **Adjacent boxes must not join.** `30`(435,633) is the Damage tile's
   price; `Attack`(643,555) is the next tile's label. The boundary between
   them is at x≈541 and nothing in the text says so.
3. **Spaces vanish in all-caps** — `ATTACKUPGRADES`. Comparison must
   normalise.
4. **Spurious boxes happen.** A phantom `'A'` at confidence **0.555**,
   centre (635, 2312), on empty screen. Real content scored 0.968–1.000, so a
   floor separates them by a wide margin — but this is one phantom across
   four images, not a distribution.

### What the screen actually looks like

Tile anchors, by template match on the fixtures:

| Tile | Anchor | Template size |
|---|---|---|
| Damage | (30, 478) | 200x160 |
| Attack Speed | (547, 478) | 200x160 |
| Critical Chance | (30, 687) | 200x160 |
| Critical Factor | (547, 687) | 200x160 |
| Unlock Cash Bonuses | (30, 478) | 780x95 |

Half-width tiles sit two to a row with a pitch of 517px horizontally and
209px vertically; unlock tiles span the full width. Each tile is a bright
bordered rectangle, and **contains further bordered rectangles** — the stat
value panel and the price panel are boxes inside the box.

The workshop tab buttons are **pictograms** — sword, shield, star, and a
fourth with no template yet — carrying no text. Two further nav entries are
padlocked. More categories are coming and none of them can be read.

## 2. Goals

- A strategy names upgrades as a guide does: `"Defense Absolute"`, not
  `workshop/row_defense_absolute.png`.
- An upgrade the bot has never seen before is buyable the first time it
  appears, with no new file and no code change.
- "Buy the highest-priority affordable row" becomes true for a whole tab,
  not merely for the part of it that fits on one screen.
- The in-run panel and the workshop are served by one mechanism.
- No coins or gems move on an unproven reader.

### Non-goals

- **Replacing template matching generally.** Screen classification, nav,
  cards, the death modal and the workshop tab icons keep their templates.
  This is about which *upgrade* to tap, nothing else.
- **Reading the death modal with OCR.** `digits.py`'s `modal` class has a
  complete `0-9` set and works. It stays.
- **A general OCR fallback chain.** There is no reader beneath this one; see
  §10.
- **The Turtle preset and the guide content.** Both are follow-on projects;
  see §15.

## 3. Architecture

Two new modules, each with one job, each testable alone.

### `ocr.py` — pixels to text

One entry point:

```python
def read(image: Image) -> tuple[TextBox, ...]
```

`TextBox` is `(text: str, confidence: float, rect: Rect)`. The module owns
three things nothing else may duplicate:

- **Engine lifecycle.** The ONNX models cost ~1–2 s to load, so the engine
  is a lazy singleton built on first use, never per scan. The bot and the
  web server share a process, so the singleton is guarded by a lock rather
  than trusting the library's thread-safety.
- **The confidence floor**, `config.OCR_CONFIDENCE_FLOOR = 0.85`. Boxes
  below it are dropped here, so no consumer ever sees a 0.555 phantom. The
  value sits in the middle of the measured gap — real content at
  0.968–1.000, the one observed phantom at 0.555 — following the same
  pick-the-middle-of-the-plateau convention `config.py` uses for
  `DIGIT_BINARY_THRESHOLD`. It is calibrated on four images and should be
  revisited against live data.
- **The number parse.** `$?digits[.digits][KMB]` and nothing else. The
  in-run wallet read back as `68 $` in the spike — symbol on the wrong side
  — and this rule turns that into a refusal rather than a guess.

`read` returns an empty tuple rather than raising, matching `digits.py`'s
rule that a bad read degrades the bot and never stops the scan loop.

### `tiles.py` — text to rows

```python
def find_tiles(image: Image) -> tuple[Rect, ...]
def read_rows(image: Image) -> tuple[Row, ...]
```

`find_tiles` detects the bordered rectangles and filters to **outer** tiles
by size, discarding the nested value and price panels. `read_rows` assigns
each `TextBox` to the tile containing its centre, then takes label and price
from proportions of that tile's own rect — the label from the left portion,
the price from the bottom-right panel.

Those proportions are **measured in task 1** (§12) against the fixtures and
written into `config.py` with the measurement recorded, the way
`PRICE_REGIONS` records what was measured when its crops were cut. The
spike's own numbers — a 270px label box within a 505px tile, so roughly the
left 53% — are the starting point, not the specification.

`Rect` here is an **absolute** `(x, y, w, h)` on the frame. It is
deliberately not `config.Region`, which is anchor-relative (`dx, dy, w, h`)
and means something different; conflating the two is how an offset gets
applied twice.

This is the existing house idiom, not a new one: `config.PRICE_REGIONS`
already expresses a price as an offset from a matched thing. The only change
is what the "matched thing" is — a detected rectangle instead of a
per-row template.

`Row` is the entire interface to the rest of the bot:

```python
@dataclass(frozen=True)
class Row:
    name: str          # normalised, matched against the strategy
    price: int | None  # None means unreadable — refuse, never guess
    tap: tuple[int, int]
    rect: Rect
    confidence: float
```

Grouping resolves both hard cases from §1 by construction. `Critical` and
`Chance` land in the same tile and join top-to-bottom. `30` at x=435 and
`Attack` at x=643 land in *different* tiles, because the boundary is a
detected rectangle edge rather than a tuned distance threshold.

### Consumers

- **`shopping.py`** — `_buy_rows` matches `Row.name` instead of locating row
  templates.
- **`tower_bot.py`** — the scan loop's four `find_and_click_image` calls
  become one `read_rows` and a name match.
- **`affordability.py`** — collapses to `price is not None and price <=
  wallet`. The brightness heuristic loses its last caller on these surfaces.

### Why not the alternatives

A **measured grid** of tile rectangles in `config.py` would fit the house
style best and be trivially testable — but it cannot express a scrolled
page. Recovering the scroll offset robustly means detecting tiles, which is
this design wearing a disguise.

**Proximity clustering** needs no measurement, but to decide whether `30` at
x=435 belongs to the left tile or the right it must know the boundary at
x≈541. The geometry does not go away; it becomes implicit and expressed as
tuned thresholds. That is a heuristic that passes on four fixtures and fails
quietly in the field, and quiet failure here means tapping the wrong
upgrade.

## 4. The map, and the buying loop

### Identity and dedupe

Rows are keyed by **name**, which is their identity — names are unique
within a tab. Overlapping swipes therefore dedupe for free, with no pixel
comparison. Bottom detection falls out of the same fact: swipe, re-read, and
if no new names appeared, the bottom has been reached. A swipe budget caps
it regardless.

That budget is **separate from `max_taps_per_visit`**. A swipe is not a
purchase and must not consume a budget that exists to bound spending.

### One step per scan

`ShoppingSession.advance()`'s contract is one capture, one decision, at most
one tap, never blocking. Mapping fits it rather than fighting it: a new
`MAP_TAB` step captures one screenful, merges it into the accumulating map,
issues one swipe, and returns. A long map is many short steps — Pause still
freezes it mid-map, and the event feed shows the tab filling in instead of
one opaque stall.

### Map first, then buy

Buying affordable rows as each screenful passes would quietly break the rule
that the **highest-priority** affordable row wins: a cheap low-priority row
two screens up gets bought before a high-priority one below is ever seen.
That rule is the entire reason for mapping. So: map the tab, pick the
target, scroll back to it, tap.

### Re-map after every purchase

A purchase changes the wallet and that row's price — and buying an unlock
tile makes **four new rows appear**, so the map is not merely stale but
structurally wrong. Patching it in place means special-casing unlocks, and
getting that wrong means buying against a phantom map.

Re-mapping is one scroll pass: trivial at today's 5 tiles, roughly four
screenfuls (~1.2 s of OCR plus swipes) on a developed account. Shopping runs
*between* runs, where nothing is time-critical. Correctness is worth more
than the pass; revisit only if it measurably hurts.

### Truncated maps

If the swipe budget is exhausted mid-map, the map ends **marked truncated**
and that is published. Buying proceeds from what was mapped — refusing
everything would be worse — but the "highest-priority affordable wins"
guarantee is void on a truncated map and must not be claimed anywhere in the
events or the dashboard.

## 5. The in-run surface

The in-run panel has tabs and scrolling too, and the Turtle build needs both
(Def Abs and Thorns on DEFENSE, Cash and Coins on UTILITY), so neither is
avoidable there.

Same machinery, **different refresh policy**. Cash accrues continuously, so
affordability changes every scan and a full map-then-buy pass would be stale
before it finished. The in-run loop keeps a **rolling map**: each scan
updates the rows currently visible and decides from what it knows; if the
highest-priority wanted row is not on screen, that scan's action is a swipe
toward it rather than a tap.

This asymmetry is reasoned from constraints, not evidence. The in-run panel
is the one surface that cannot be rehearsed the way `enabled`-without-`armed`
rehearses shopping, because in-run cash is spent by the same tap that tests
it. Mitigating factors: in-run cash **resets every run**, so a wrong tap
there costs nothing permanent — which is precisely why `navigate.py` could
claim its original invariant. The refresh policy is isolated to one decision
so that revisiting it after a live session reshapes nothing else.

## 6. Strategy schema

A row loses everything that existed to serve template matching.

| Field | Fate |
|---|---|
| `name` | **Stays** — now the identity, matched against OCR |
| `category` | **Stays** — see below |
| `enabled` | Stays |
| `template` | Removed |
| `threshold` | Removed — one global confidence floor in `ocr.py` |
| `brightness_ratio` | Removed — no caller left |
| `layout` | Removed — derived from a detected tile's width |

What remains is `{name, category, enabled}` — exactly the triple
`config.SHOPPING_ROWS` already uses, and for the reason that file already
gives for keeping the others out of it: "a second place to keep correct, and
the two could drift."

**`category` matters more than before.** It is not derivable from OCR: a row
on the DEFENSE tab is invisible until that tab is open, so category is what
tells the bot which tabs to open and in what order
(`Shopping.categories_in_priority_order()`).

**`ActionRule` gains `category`.** The in-run panel has tabs as well;
today's four actions all happen to sit on ATTACK, which is why the field was
never needed.

**Top-level `affordability` is removed.** With the price arriving on the
`Row`, `"digits"` versus `"brightness"` no longer describes a real choice on
these surfaces.

### Name matching: normalise, then exact

Case-fold and strip spaces and punctuation on both sides, then require an
exact hit against the names in the strategy. Normalisation is not optional —
`ATTACKUPGRADES` loses its space in all-caps text.

**No fuzzy or edit-distance matching.** A near-match that resolves to the
wrong row spends coins, and the entire safety argument is that only a name
on the list can ever be tapped. A mangled read must refuse.

The cost is that a row OCR consistently garbles would never be bought. That
must not be silent: **unmatched text is published as an event carrying the
raw string**, the way `PurchaseSkipped` already reports refusals. A
persistent mismatch appears in the feed as "read `Criticai Chance`, matched
nothing" rather than as a row that mysteriously never buys.

### Migration: a hard break

`strategies/` holds exactly `default.json` plus an `.active` naming it. The
blast radius is one file, which is rewritten as part of this change.

`Strategy.from_dict` already hard-rejects unknown fields
(`unknown action field 'template'`), so a hand-written profile fails loudly
with the offending field named. That is the right outcome: silently ignoring
a `threshold` someone tuned would leave them believing it still does
something.

## 7. What gets deleted

- The 14 template PNGs — `templates/upgrade_*.png` and
  `templates/workshop/row_*.png`, `unlock_*.png`.
- The template and layout columns of `config.WORKSHOP_ROWS`.
- `config.PRICE_REGIONS`, `strategy.DEFAULT_ROW_THRESHOLD`,
  `strategy._TEMPLATE_TO_LAYOUT`.
- Both layout-contradiction checks in `ShoppingRule.__post_init__`.
- The body of `Strategy.validated()`.

Those checks are careful, subtle code guarding one failure mode — reading a
real price region at the wrong offset because a row's declared layout
contradicts the layout its template was measured with. That mode stops
existing when tiles are detected rather than assumed, so the guard is
deleted rather than ported.

## 8. Security

`validated()`'s containment check exists because a template arrives in a
request body and is handed to `cv2.imread`, so an unchecked one lets the
client choose which file on disk the bot reads. A **name cannot address the
filesystem**, so that surface disappears rather than being defended.

`validate_name` on profile names is unaffected and stays — profile names
still become filenames.

## 9. Shipped default order

`strategies/default.json` is rewritten to `{name, category, enabled}` rows
and otherwise keeps the order the shopping design chose and justified: the
three unlock tiles first, then defence, then attack, with the crit rows
present and disabled.

The order is unchanged because the reasoning behind it is unchanged. What
changes is that rows may now be *added* to it by name, without a PNG — which
is what the Turtle preset (§15) needs.

## 10. Failure policy

Refuse, publish, keep scanning.

| Failure | Response |
|---|---|
| OCR engine will not load | **Startup gate**, as `build_shopping()` gates on the header atlas. Buying is disabled and the reason published; the bot still navigates, classifies screens and records runs. |
| `find_tiles` finds nothing | No rows, no tap. Publish a skip. |
| Boxes read, none match a known name | Publish the **raw string** (§6). |
| Matched row, unreadable price | Refuse that row (`unreadable`), as today. |
| Confidence below floor | Dropped in `ocr.py`; consumers never see it. |
| Swipe budget exhausted mid-map | End the map, mark it truncated, publish (§4). |
| Tap budget exhausted | End the visit, as today. |
| A step raises | Ends the visit, not the bot, as today. |

**There is no fallback beneath OCR.** Brightness was the old degraded path
and loses its last caller here. The honest position is that a broken engine
means no buying at all — announced loudly at startup rather than discovered
as silent inaction.

## 11. Risks

| Risk | Mitigation |
|---|---|
| **`find_tiles`' size filter does not separate outer tiles from the nested value/price panels.** The whole approach rests on this and it is unvalidated — nested borders were seen in a screenshot, not proven separable. | First task in the plan, before anything is built on it. If it fails, the measured-grid alternative (§3) is the fallback and this design changes. |
| A confident-but-wrong OCR price approves a purchase. | Dry-run A/B against the existing reader before arming (§12). Confidence floor, strict parse, `price <= wallet`, `armed`. |
| Confidence floor tuned on one phantom across four images. | Floor sits in a wide gap (0.555 vs 0.968). Revisit with live data; the dry-run surfaces low-confidence boxes. |
| OCR garbles a row consistently, so it is never bought. | Unmatched raw text is published (§6), making it visible rather than silent. |
| Whole-frame OCR at 213–333 ms is too slow in-run. | 2.0 s interval leaves headroom. If it bites, crop to the panel region — a change local to the caller. |
| RapidOCR output varies across library versions. | Pin the version. Tests assert text and confidence *thresholds*, never exact confidences. |
| More tabs are coming (a fourth pictogram, two padlocked nav entries) and none can be read. | Tab icons stay template-matched; a new tab needs a new template, which is a known and bounded cost. |

## 12. Implementation order

"Replace entirely" is the end state, not a single commit. Deleting the
templates before OCR has been watched on a live account would leave nothing
to compare against.

**Phase 1 — add, do not remove.**

1. Validate `find_tiles`' size filter against all four fixtures. **If this
   fails, stop and revisit the design.**
2. `ocr.py` — engine singleton, confidence floor, number parse.
3. `tiles.py` — `find_tiles`, `read_rows`, the `Row` type.
4. Wire `shopping.py` to `Row`, keeping the template path alongside.
5. Dry-run A/B: `enabled` without `armed`, reading each price **both** ways
   and logging disagreements. Real prices, zero coins at risk.

Phase 1 reads **only the visible screenful** — scrolling arrives in Phase 2.
That is enough to rehearse the reader, because today's tabs hold at most
five tiles and all of them fit. It does mean Phase 1 cannot yet honour
"highest-priority affordable wins" across a full tab, and must not claim to.

**Phase 2 — remove, once the rehearsal is clean.**

6. Scroll-and-map (`MAP_TAB`, swipe budget, truncation).
7. In-run rolling map, `ActionRule.category`, tab switching in-run.
8. Schema cut-over and `default.json` rewrite.
9. Delete everything in §7.

The comparison path in step 5 is **deliberate throwaway scaffolding** that
step 9 deletes. Naming it as such now is what stops it becoming permanent by
accident.

## 13. Testing

The module split is what keeps the suite fast. `ocr.py` tests run the engine
(~300 ms a frame plus model load), so there are few. `tiles.py` tests take
**recorded `TextBox` fixtures as JSON** and run instantly with no engine.

- `find_tiles` against all four fixtures: outer tile rects asserted, nested
  value and price panels absent. This is task 1 (§12).
- `read_rows` on the two cases the spike proved hard: `Critical` + `Chance`
  join; `30` at x=435 binds to Damage, not Attack Speed.
- Normalisation: `ATTACKUPGRADES` matches; `Criticai Chance` refuses.
- Number parse: `68 $` refuses; `$10` → 10; `1.77K` → 1770.
- Map state machine on synthetic overlapping screenfuls: dedupe by name,
  bottom detection when no new names appear, truncation flag on budget
  exhaustion. No scrolled fixtures exist, so these are constructed by
  slicing a tall synthetic image; **real scrolled captures should be taken
  in the first live session.**
- Schema: a file carrying `template` fails naming that field; a
  `{name, category, enabled}` file loads.

Assert on text and on confidence *thresholds*, never on exact confidence
values — they are ONNX inference outputs and pinning them makes the suite
brittle across versions.

## 14. Dependencies

Adds `rapidocr-onnxruntime` (pip-installable, bundles ~15 MB of ONNX models,
no system binary, runs offline). Pinned.

Rejected: `pytesseract` needs a system `tesseract` binary, an install step
this repo does not currently have. `easyocr` and `paddleocr` drag in torch.

## 15. Follow-on projects

This spec is one of three. Each gets its own spec, plan and implementation
cycle.

- **B — Predefined strategies.** A read-only preset catalog plus "New from
  preset", and the Tier 1 Turtle preset itself. The *mechanism* depends on
  nothing; the Turtle preset's **rows** depend on this spec, because
  Defense Absolute, Thorns, Cash Bonus and Coins/Kill Bonus have no
  templates and cannot be named until upgrades are addressed by name.
- **C — Guide content.** The Tier Progression ladder and the Turtle build
  write-up on the `/guide` page. Depends on nothing and can ship at any
  time.
