# OCR Row Addressing — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the template path from the workshop shopping surface, so OCR
is the only reader there, and make a row OCR cannot match visible in the event
feed instead of silently unbought.

**Architecture:** The cut-over already happened — `_buy_rows` addresses rows by
name off `tiles.read_rows` and takes price and tap point from the `Row`. What
remains is subtraction: delete the observation scaffolding that earned the
cut-over, delete the schema fields and PNGs that only ever fed template
matching, and add the one event §6 requires so a garbled read is loud. No new
reading machinery is built here.

**Tech Stack:** Python 3.12, OpenCV, RapidOCR (ONNX), pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-ocr-row-addressing-design.md`

**Phase 1 plan:** `docs/superpowers/plans/2026-09-04-ocr-row-addressing-phase-1.md`

## Global Constraints

- Emulator resolution is **1080x2400**. Every fixture is at that size and
  `cv2.matchTemplate` is not scale-invariant.
- **`config.OCR_CONFIDENCE_FLOOR = 0.85`** — boxes below it are dropped
  inside `ocr.py` and never reach a consumer.
- Pin **`rapidocr-onnxruntime==1.4.4`**.
- Every reader returns empty/`None` rather than raising. A bad read degrades
  the bot; it never stops the scan loop.
- **Name matching is normalise-then-exact. No fuzzy or edit-distance
  matching** (spec §6): a near-match that resolves to the wrong row spends
  coins.
- Run tests with `-p no:allure_pytest` and an explicit long Bash timeout.
  **Never run the whole suite** — run the files this plan names.
- **The Cards surface stays on the glyph reader.** `_buy_cards` reads card
  prices through `self._reader` against `config.CARD_PRICE_REGION` and the
  `"menu"` atlas (`shopping.py:829`). `digits.py`, `templates/atlas/menu/`
  and `CARD_PRICE_REGION` must survive every deletion in this plan. Cards
  are not in the OCR spec's scope.

---

## What the rehearsal settled

Phase 1's plan said to write this one "after the rehearsal, from what it
found". It found more than expected, and three of its findings change what
Phase 2 should contain.

| Finding | Evidence | Consequence for this plan |
|---|---|---|
| OCR reads prices the atlas refuses, and the bot buys them | Live: `Attack Speed: the template reader could not read a price, OCR read 92` → `Purchased` | The cut-over is justified. Deletion can proceed. |
| Both tap points in the codebase were no-ops | Live: label tap and tile-centre tap each left coins and price unchanged; the price-strip tap bought | Already fixed (`3893081`, `ba4248e`). Not in this plan. |
| Overlays blind the reader; modals swallow taps; arrival must not depend on a row template | Live: 4 rows written off behind an info panel; a visit spent on a tab it was standing on | Already fixed (`ba4248e`). Not in this plan. |
| **No workshop tab overflows its screenful** | ATTACK 7 tiles, DEFENSE 5, UTILITY 3, all read in one frame; a swipe on ATTACK revealed **no** new rows | **Scroll-and-map is deferred.** See "Deferred" below. |
| OCR mangles some row names | Live read: `'Damage / Meter C'` — the coin glyph joined the name | §6's unmatched-text event is needed now, not theoretical. Task 1. |
| A new event needs **no** database migration | `sinks/store.py:48` `to_row` flattens any event; unknown fields go to the JSON `detail` blob, and `_TYPED` is a fixed 6-column list | Phase 1 made the A/B log-only to avoid "schema churn in `events.py` and `db.py`". That reason was wrong. Task 1 costs one dataclass. |
| The header balance is rounded to 3 significant figures past 1K | `1.77K`/`1.74K` for a 30-coin purchase — true delta anywhere in 20–40 | Affordability compares against a ±5 estimate. **Not fixed here**: both readers see the same rounded text, so this is the game's display, not a reader defect. Recorded so nobody "discovers" it as a regression. |

## Scope

**In this plan:** the unmatched-row event, deletion of the A/B scaffolding,
the workshop row schema cut-over, and deletion of the workshop templates and
everything that served them.

**Deferred, with the trigger that should start each:**

- **Scroll-and-map (spec §4).** Every tab fits one screenful today and a
  swipe reveals nothing, so `MAP_TAB`, the swipe budget, bottom detection and
  the truncated-map flag cannot be built against evidence — only against
  guesses, which is the failure mode Phase 1's plan refused for exactly this
  reason. **Trigger:** Task 1's `RowUnmatched` fires for a row you know is on
  that tab. That is the overflow tripwire, and it costs nothing extra — a row
  below the fold is a row OCR did not read. Until then, "highest-priority
  affordable wins" holds trivially because every row is visible.
- **The in-run surface (spec §5), `ActionRule.category`, and deleting
  `templates/upgrade_*.png`.** Spec §5 admits its refresh policy is "reasoned
  from constraints, not evidence", and the in-run panel is the one surface
  that cannot be rehearsed — in-run cash is spent by the same tap that tests
  it. **Trigger:** a deliberate watched in-run session, the way the workshop
  buy point needed one. Note `tiles.read_rows` already read the in-run panel
  correctly when it was hit by accident during the rehearsal (`Damage $10`,
  `Attack Speed $5`, `Critical Chance $4`, `Critical Factor $10`), so the
  reader is not the open question — the refresh policy is.
- **Top-level `affordability` (spec §6 says remove it).** It still selects
  the in-run affordability check in `build_affordability`, which serves the
  deferred surface above. Removing it here would break the in-run path.
  **Trigger:** the in-run cut-over.

Each deferred item is a plan of its own when its trigger fires. This plan
produces working, testable software without them.

## File Structure

| File | Responsibility after this plan |
|---|---|
| `events.py` (modify) | Gains `RowUnmatched`. No other change. |
| `shopping.py` (modify) | Loses `compare_readers`, `ab_logger` and the template read in `_buy_rows`. Publishes `RowUnmatched`. `_buy_cards` untouched. |
| `strategy.py` (modify) | `ShoppingRule` becomes `{name, category, enabled}`. `validated()` stops checking shopping templates. `DEFAULT_ROW_THRESHOLD` and `_TEMPLATE_TO_LAYOUT` deleted. |
| `config.py` (modify) | `WORKSHOP_ROWS`, `PRICE_REGIONS` and `LAYOUTS` deleted. `PRICE_REGION` (singular, in-run) and `CARD_PRICE_REGION` **stay**. |
| `strategies/default.json` (modify) | Workshop rows rewritten to the triple. |
| `templates/workshop/row_*.png`, `unlock_*.png` (delete) | Nothing reads them. |
| `tests/test_ocr_comparison.py` (delete) | Tested the A/B that Task 2 removes. |

---

### Task 1: Publish what OCR read but could not match

Spec §6 requires that a row OCR consistently garbles "must not be silent:
unmatched text is published as an event carrying the raw string". Today that
information exists only in the A/B's log line, which Task 2 deletes — so this
task comes first, or the information is lost between the two.

The rehearsal proves the need rather than assuming it: OCR read
`'Damage / Meter C'` on the live ATTACK tab, the coin glyph having joined the
name. A row named that way in a strategy would never match, and nothing in
the feed would say why.

**Files:**
- Modify: `events.py` (add the event beside `PurchaseSkipped`, ~line 155)
- Modify: `shopping.py` (`_buy_rows`, the `seen is None` branch)
- Test: `tests/test_shopping.py`

**Interfaces:**
- Consumes: `tiles.Row`, `shopping._row_named` (both exist).
- Produces: `events.RowUnmatched(item: str, read: tuple[str, ...])`.
  Task 2 relies on this event existing, because it deletes the log line that
  carries the same information today.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_shopping.py`, at the end of the file:

```python
def test_a_row_ocr_could_not_match_is_published_with_what_was_read(
    session, fake_header
) -> None:
    """A garbled name must be loud, not merely unbought.

    Live evidence that this is real and not defensive: OCR read
    'Damage / Meter C' off the ATTACK tab, the coin glyph having joined the
    row name. A row spelled that way in a strategy would never match, and
    without this event the feed would show nothing at all - the row would
    simply never be bought, forever, for no visible reason.

    menu_workshop_utility_restocked.png is the honest version of the same
    shape: the configured row was bought in an earlier session and is gone,
    and three other rows are on the page.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=(
        ShoppingRule(name="Unlock Cash Bonuses",
                     template="workshop/unlock_cash_bonuses.png",
                     category="UTILITY", layout="tile"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_utility_restocked"), device, policy)

    unmatched = session._bus.of_type("RowUnmatched")
    assert unmatched, "nothing said why the row was never bought"
    assert unmatched[0].item == "Unlock Cash Bonuses"
    assert unmatched[0].read == (
        "Cash Bonus", "Cash / Wave", "Unlock Coin Bonuses",
    )
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run pytest tests/test_shopping.py -q -p no:allure_pytest \
    -k "could_not_match" 2>&1 | tail -8
```

Expected: `AssertionError: nothing said why the row was never bought`, from
`of_type("RowUnmatched")` returning `[]`. If instead you get
`AttributeError` on `events.RowUnmatched`, you wrote the implementation
first — revert it and start again.

- [ ] **Step 3: Add the event**

In `events.py`, directly after the `PurchaseSkipped` dataclass:

```python
@dataclass(frozen=True, kw_only=True)
class RowUnmatched(Event):
    """A configured row was not among the rows OCR read off the page.

    Two very different things look identical without this: a row that has
    been bought and is gone from the page (normal, permanent), and a row
    whose name OCR garbles on every scan (a defect that would otherwise
    present as a row that mysteriously never buys). `read` carries the raw
    strings so the difference is visible in the feed rather than guessed at.

    Names are published verbatim - NOT normalised. Normalisation is what
    hid the difference; `'Damage / Meter C'` is the whole point.
    """

    item: str
    read: tuple[str, ...]
```

No database change is needed: `sinks/store.py`'s `to_row` flattens any event
and puts every field outside its fixed `_TYPED` list into the JSON `detail`
blob.

- [ ] **Step 4: Publish it**

In `shopping.py`, in `_buy_rows`, the `seen is None` branch currently reads:

```python
        if seen is None:
            # Not on this tab, or not on the visible screenful - Phase 2's
            # scroll-and-map is what will tell those two apart.
            self._bus.publish(events.PurchaseSkipped(item=rule.name, reason="no_match"))
            self._exhausted.add(rule.name)
            return
```

Replace with:

```python
        if seen is None:
            # Bought and gone, garbled by OCR, or below the fold on a tab
            # that has outgrown one screenful. RowUnmatched carries what was
            # actually read so those stay distinguishable in the feed - and
            # it is the tripwire for the third case, which is the trigger
            # for building scroll-and-map (spec §4).
            self._bus.publish(events.RowUnmatched(
                item=rule.name, read=tuple(row.name for row in visible),
            ))
            self._bus.publish(events.PurchaseSkipped(item=rule.name, reason="no_match"))
            self._exhausted.add(rule.name)
            return
```

- [ ] **Step 5: Run the test and its neighbours**

```bash
uv run pytest tests/test_shopping.py tests/test_shopping_events.py \
    tests/test_store_sink.py -q -p no:allure_pytest 2>&1 | tail -5
```

Expected: all pass. `test_store_sink.py` is included deliberately — it is
what would catch a new event breaking the flattening.

- [ ] **Step 6: Commit**

```bash
git add events.py shopping.py tests/test_shopping.py
git commit -m "feat: publish what OCR read when a row does not match

A row that is bought and gone, a row OCR garbles, and a row below the fold
all present identically today: nothing happens, forever. RowUnmatched
carries the raw strings that were read, so the three stay distinguishable -
and so a tab that outgrows one screenful announces itself instead of quietly
voiding the highest-priority-affordable-wins rule.

Live evidence it is not defensive: OCR reads 'Damage / Meter C' on the
ATTACK tab, the coin glyph having joined the row name.

Needs no migration - sinks/store.py flattens any event into the detail blob."
```

---

### Task 2: Delete the A/B scaffolding

Phase 1 called this "deliberate throwaway scaffolding" and said naming it so
was "what stops it becoming permanent by accident". It has served its
purpose: the rehearsal logged
`Attack Speed: the template reader could not read a price, OCR read 92`
immediately before a successful purchase.

It is now actively misleading. OCR decides every price, so `compare_readers`
compares the deciding reader against one no decision consults, and the
comparison costs a template match plus a glyph read per row per scan.

**Files:**
- Modify: `shopping.py` (delete `compare_readers`, `ab_logger`, the `try`
  block in `_buy_rows`, and the `match`/`price` template read above it)
- Delete: `tests/test_ocr_comparison.py`
- Test: `tests/test_shopping.py`

**Interfaces:**
- Consumes: `events.RowUnmatched` from Task 1 — it carries what the deleted
  log line reported.
- Produces: `_buy_rows` no longer touches `self._templates` or
  `self._reader`. Task 4 relies on that: it deletes the PNGs and
  `config.PRICE_REGIONS` those two lines were the last readers of.

- [ ] **Step 1: Write the failing test**

The test that proves the template path is gone is one where the row's
template file does not exist. Add to `tests/test_shopping.py`:

```python
def test_a_row_is_bought_without_its_template_file(session, fake_header) -> None:
    """Nothing in the workshop buy path reads a row template any more.

    The rule still carries a `template` field until the schema cut-over, so
    this points it at a file that is not on disk: if any code path still
    loads it, the purchase cannot happen. Tab and nav templates are
    untouched and still load - only the ROW templates are gone.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=(
        ShoppingRule(name="Damage", template="workshop/row_damage.png",
                     category="ATTACK"),
    ))
    session._templates = _NoRowTemplates(config.TEMPLATE_DIR)
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)

    bought = session._bus.of_type("Purchased")
    assert bought and bought[0].item == "Damage"
    assert bought[0].price == 30
```

And, above the tests in the same file, the cache that refuses row templates:

```python
class _NoRowTemplates(vision.TemplateCache):
    """A template cache with the workshop ROW templates removed.

    Deleted-file behaviour without deleting a file: anything under
    workshop/row_ or workshop/unlock_ raises, everything else (nav buttons,
    tab pictograms, page anchors) loads normally.
    """

    def get(self, name: str):
        if name.startswith("workshop/row_") or name.startswith("workshop/unlock_"):
            raise AssertionError(f"the buy path still reads a row template: {name}")
        return super().get(name)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run pytest tests/test_shopping.py -q -p no:allure_pytest \
    -k "without_its_template_file" 2>&1 | tail -8
```

Expected: `AssertionError: the buy path still reads a row template:
workshop/row_damage.png`, raised from the `vision.locate_template` call that
feeds the A/B.

- [ ] **Step 3: Delete the scaffolding**

In `shopping.py`, delete the whole block from the
`# --- Phase 1 A/B scaffolding ---` banner through the end of
`compare_readers`, including `ab_logger`. Then in `_buy_rows`, delete the
template read and the comparison — everything from the comment beginning
`# The template match is still taken` down to the closing of the `try`:

```python
        # The template match is still taken, for the observation below only.
        # Nothing decides on it any more.
        match = vision.locate_template(screen, self._templates.get(rule.template), rule.threshold)
        # Read the price BEFORE the early returns rather than after, so the
        # ... (comment block)
        price = (
            None if match is None
            else self._reader.read(screen, config.PRICE_REGIONS[rule.layout], match.top_left, "menu")
        )

        # Phase 1 A/B - observation only, never a decision. Deleted in
        # Phase 2. Wrapped because a reader under evaluation must not be
        # able to break the reader in production.
        try:
            note = compare_readers(rule, price, visible)
            if note is not None:
                ab_logger.warning("%s", note)
            else:
                ab_logger.info("%s: readers agree on %s", rule.name, price)
        except Exception:
            ab_logger.exception("the OCR comparison itself failed")
```

Nothing replaces it. `price = seen.price` a few lines below already supplies
the only price any decision uses.

- [ ] **Step 4: Delete the test file**

```bash
git rm tests/test_ocr_comparison.py
```

It tests `compare_readers` and nothing else.

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_shopping.py tests/test_shopping_loop.py \
    tests/test_shopping_prices.py tests/test_tiles.py \
    -q -p no:allure_pytest 2>&1 | tail -5
```

Expected: all pass. If `test_shopping_prices.py` fails, read it before
changing it — it may be asserting the template price path, which is the thing
being deleted, or it may be covering `_buy_cards`, which must keep working.

- [ ] **Step 6: Commit**

```bash
git add -A shopping.py tests/
git commit -m "refactor: delete the Phase 1 A/B scaffolding

It served its purpose: the rehearsal logged 'the template reader could not
read a price, OCR read 92' immediately before the purchase that line
justifies. Keeping it now means comparing the deciding reader against one no
decision consults, at the cost of a template match and a glyph read per row
per scan.

What it reported that still matters - what OCR read when nothing matched -
is an event now (RowUnmatched), not a log line.

This removes the last reader of a workshop row template."
```

---

### Task 3: Cut the workshop row schema down to name, category, enabled

Spec §6: a row loses everything that existed to serve template matching.
`template`, `threshold`, `brightness_ratio` and `layout` all go, leaving
exactly the triple `config.SHOPPING_ROWS` already holds.

`Strategy.from_dict` hard-rejects unknown fields, so a hand-written profile
carrying a `threshold` someone tuned fails loudly with the field named,
rather than silently ignoring it. Spec §6 calls that the right outcome and
this task keeps it.

**Files:**
- Modify: `strategy.py` (`ShoppingRule` ~282-360, `DEFAULT_ROW_THRESHOLD`
  ~250, `_TEMPLATE_TO_LAYOUT` ~268, `validated()` ~737)
- Modify: `strategies/default.json`
- Test: `tests/test_strategy_shopping.py`, `tests/test_strategy.py`,
  `tests/test_shopping.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2 except that `_buy_rows` no longer reads
  `rule.template`, `rule.threshold` or `rule.layout` — Task 2 removed the
  last use of all three.
- Produces: `ShoppingRule(name: str, category: str, enabled: bool = True)`.
  Task 4 relies on `layout` being gone before it deletes `config.LAYOUTS`
  and `config.PRICE_REGIONS`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_strategy_shopping.py`:

```python
def test_a_shopping_row_is_just_a_name_a_category_and_a_switch() -> None:
    """Spec §6: a row loses everything that existed to serve template
    matching. What remains is the triple config.SHOPPING_ROWS already holds,
    and for the reason that file already gives - a second place to keep
    correct, and the two could drift."""
    rule = ShoppingRule(name="Damage", category="ATTACK")
    assert rule.enabled is True
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
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run pytest tests/test_strategy_shopping.py -q -p no:allure_pytest \
    -k "just_a_name or retired_field" 2>&1 | tail -8
```

Expected: the first fails on `hasattr(rule, "template")` being True (and on
`ShoppingRule(name=..., category=...)` raising for a missing `template`
argument); the second fails because `threshold` is currently a known field.

- [ ] **Step 3: Cut the dataclass down**

In `strategy.py`, replace the `ShoppingRule` body (fields and
`__post_init__`) with:

```python
@dataclass(frozen=True)
class ShoppingRule:
    """One menu row the bot may buy.

    `name` is the identity: it is matched, normalised, against the row names
    OCR reads off the page (see shopping._row_named). Nothing else addresses
    a row any more.

    `category` is the one thing OCR cannot supply. A row on the DEFENSE tab
    is invisible until that tab is open, so category is what tells the bot
    which tabs to open and in what order - see
    Shopping.categories_in_priority_order().
    """

    name: str
    category: str
    enabled: bool = True

    def __post_init__(self) -> None:
        _check_types(_own_values(self), _SHOPPING_RULE_TYPES)
        if not self.name:
            raise ControlError("name", "a shopping row needs a name")
        if self.category not in CATEGORIES:
            raise ControlError("category", f"category must be one of {CATEGORIES}")
```

Update `_SHOPPING_RULE_TYPES` to `{"name": str, "category": str, "enabled": bool}`.
Delete `DEFAULT_ROW_THRESHOLD` and `_TEMPLATE_TO_LAYOUT` — this was their only
caller.

- [ ] **Step 4: Stop validating shopping templates**

In `strategy.py`'s `validated()`, delete the shopping loop and the paragraph
of docstring describing it:

```python
        for rule in self.shopping.workshop:
            _check_template(rule.name, rule.template)
```

The `self.actions` loop **stays** — in-run actions still carry templates
until the deferred in-run cut-over. Spec §7 says the whole body goes; it can
only go when both surfaces have moved, and only one has.

- [ ] **Step 5: Rewrite the shipped profile**

In `strategies/default.json`, each entry of `shopping.workshop` becomes the
triple. Order is unchanged — spec §9 keeps the order the shopping design
chose and justified:

```json
  "workshop": [
    { "name": "Unlock Cash Bonuses", "category": "UTILITY", "enabled": true },
    { "name": "Unlock Defense Upgrades", "category": "DEFENSE", "enabled": true },
    { "name": "Unlock Range Upgrades", "category": "ATTACK", "enabled": true },
    { "name": "Health", "category": "DEFENSE", "enabled": true },
    { "name": "Health Regen", "category": "DEFENSE", "enabled": true },
    { "name": "Damage", "category": "ATTACK", "enabled": true },
    { "name": "Attack Speed", "category": "ATTACK", "enabled": true },
    { "name": "Critical Chance", "category": "ATTACK", "enabled": false },
    { "name": "Critical Factor", "category": "ATTACK", "enabled": false }
  ],
```

Leave top-level `affordability` alone. Spec §6 removes it; it still selects
the in-run affordability check, which serves the deferred surface. See
"Scope".

- [ ] **Step 6: Run the tests**

```bash
uv run pytest tests/test_strategy_shopping.py tests/test_strategy.py \
    tests/test_strategy_store.py tests/test_shopping.py \
    tests/test_control.py tests/test_control_api.py \
    tests/test_strategies_api.py tests/test_gating.py \
    -q -p no:allure_pytest 2>&1 | tail -8
```

Expect several failures in the test files, not the code: every
`ShoppingRule(...)` constructed with `template=` or `layout=` needs those
arguments dropped. Fix them by deletion, not by adding compatibility
shims. `tests/test_strategy_store.py` has one test that reads the real
committed `strategies/default.json` — it should now agree with the rewritten
file.

- [ ] **Step 7: Commit**

```bash
git add strategy.py strategies/default.json tests/
git commit -m "refactor: a shopping row is a name, a category and a switch

Spec §6. template, threshold, brightness_ratio and layout all existed to
serve template matching, and nothing has matched a row template since the
A/B came out. What remains is the triple config.SHOPPING_ROWS already holds.

validated() keeps its actions loop: in-run upgrades still carry templates
until that surface is cut over, so spec §7's 'the body of validated()' can
only go half way here.

Top-level affordability stays for the same reason - it still selects the
in-run check."
```

---

### Task 4: Delete the templates and the geometry that served them

Spec §7's deletion list, minus the in-run half.

**Files:**
- Delete: `templates/workshop/row_*.png`, `templates/workshop/unlock_*.png`
- Modify: `config.py` (`WORKSHOP_ROWS`, `PRICE_REGIONS`, `LAYOUTS`)
- Modify: `README.md`
- Test: `tests/test_shopping_templates.py`, `tests/test_fixtures.py`

**Interfaces:**
- Consumes: Task 2 removed the last reader of `PRICE_REGIONS`; Task 3
  removed the last reader of `LAYOUTS` and `WORKSHOP_ROWS`.
- Produces: nothing new. This task only subtracts.

- [ ] **Step 1: Prove nothing reads them**

Before deleting, confirm it — a grep is cheaper than a broken run:

```bash
grep -rn "WORKSHOP_ROWS\|PRICE_REGIONS\|LAYOUTS" *.py tools/*.py sinks/*.py web/ 2>/dev/null
```

Expected: no hits outside `config.py` itself. `PRICE_REGION` (singular, the
in-run price box used by `config.buy_point`) and `CARD_PRICE_REGION **will**
appear if you grep loosely — they stay. If anything else appears, stop:
a task above is incomplete.

- [ ] **Step 2: Write the failing test**

Four tests in `tests/test_shopping_templates.py` exist only to police the row
templates and go with them. Delete, by name:

- `test_every_row_is_found_on_its_own_tab` — reads `config.WORKSHOP_ROWS[name]`
- `test_rows_are_absent_from_the_other_tabs` — the cross-match guard; a name
  cannot cross-match, so the failure mode is gone, not merely untested
- `test_every_row_declares_a_layout_that_exists` — reads `config.LAYOUTS` and
  `config.PRICE_REGIONS`
- `test_unlock_tiles_use_the_tile_layout` — reads the layout column

And amend `test_every_configured_template_exists_on_disk` (~line 204), which
builds its list from `config.WORKSHOP_ROWS.values()`. Drop that line, keeping
the tabs and card buttons, which are still template-matched:

```python
def test_every_configured_template_exists_on_disk(cache) -> None:
    paths = list(config.WORKSHOP_TABS.values()) + list(config.CARD_BUTTONS.values())
    for path in paths:
        assert cache.get(path) is not None, f"missing template: {path}"
```

Then add the inverse of what the deleted tests asserted:

```python
def test_no_workshop_row_templates_remain() -> None:
    """Spec §7. A row is addressed by name now; a PNG of its label is a
    thing to keep in sync with a font, for no reader.

    The workshop directory itself stays - the tab pictograms live there and
    are still template-matched, because a tab icon carries no text to read.
    """
    workshop = config.TEMPLATE_DIR / "workshop"
    assert workshop.is_dir()
    strays = sorted(
        p.name for p in workshop.glob("*.png")
        if p.name.startswith(("row_", "unlock_"))
    )
    assert strays == [], f"still on disk: {strays}"
    assert (workshop / "tab_attack.png").is_file(), "tab pictograms must stay"
```

- [ ] **Step 3: Run it and watch it fail**

```bash
uv run pytest tests/test_shopping_templates.py -q -p no:allure_pytest \
    -k "no_workshop_row_templates" 2>&1 | tail -6
```

Expected: `assert [...] == []` listing the row and unlock PNGs.

- [ ] **Step 4: Delete**

```bash
git rm templates/workshop/row_*.png templates/workshop/unlock_*.png
```

Then in `config.py` delete `WORKSHOP_ROWS`, `PRICE_REGIONS` and `LAYOUTS`
entirely, including their comment blocks. Keep `PRICE_REGION`,
`buy_point()`, `CARD_PRICE_REGION`, `WORKSHOP_TABS`, `NAV_TARGETS` and
`SHOPPING_ROWS`.

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_shopping_templates.py tests/test_fixtures.py \
    tests/test_shopping.py tests/test_strategy.py tests/test_nav_templates.py \
    tests/test_gating.py -q -p no:allure_pytest 2>&1 | tail -8
```

Expected: all pass. `tests/test_fixtures.py` and `tests/test_nav_templates.py`
are here because they walk the template directory and would notice files
vanishing.

- [ ] **Step 6: Update the README**

Two sections are now wrong. In the "Three things here are still unverified on
a live device" block, the `menu` atlas paragraph describes a gap that no
longer affects the workshop — prices come from OCR. Replace its last two
sentences with:

```markdown
> The `menu` atlas gap no longer affects workshop prices: those are read by
> OCR (`tiles.read_rows`), which read `92` off a live Attack Speed row that
> the atlas refused, and bought it. The atlas is still what reads CARD
> prices, which are not part of the OCR row-addressing work, so the gap
> stays real there.
```

In "Known gap: no suffix glyphs in the wallet or price atlas", the paragraph
beginning "The `menu` atlas - the size class `shopping.py` reads every
workshop and card price at" is now half wrong. Change "every workshop and
card price" to "every card price", and delete the sentence about
`_buy_rows`.

- [ ] **Step 7: Commit**

```bash
git add -A templates/ config.py README.md tests/
git commit -m "refactor: delete the workshop row templates and their geometry

Spec §7. Fourteen PNGs of row labels, the price regions measured from them,
and the layout enum that said which region to use - all of it existed so a
row could be found without reading it. Nothing has read them since the A/B
came out.

The workshop template directory stays: tab pictograms are still
template-matched, because a tab icon carries no text to read. PRICE_REGION
(in-run) and CARD_PRICE_REGION stay - different surfaces, neither cut over."
```

---

## Self-review

**Spec coverage.** §4 scroll-and-map: deferred, with evidence and a trigger —
see "Scope". §5 in-run: deferred, same. §6 schema: Task 3, except top-level
`affordability`, deferred with its reason; name matching normalise-then-exact
already ships in `shopping._row_named`; unmatched text published in Task 1.
§7 deletions: Task 4, minus the in-run half of `validated()` and
`templates/upgrade_*.png`, both deferred with Task 3 Step 4 saying so. §9
shipped order: preserved verbatim in Task 3 Step 5. §10 startup gate: landed
early, in `ba4248e`'s predecessor `aaf8b96`. §12 items 6 and 7: deferred.
Items 8 and 9: Tasks 3 and 4.

**Placeholders.** None: every step names exact files, and every code step
carries the code.

**Type consistency.** `events.RowUnmatched(item: str, read: tuple[str, ...])`
is defined in Task 1 Step 3 and used in Task 1 Steps 1 and 4 with those
names. `ShoppingRule(name, category, enabled)` is defined in Task 3 Step 3;
Task 2's test still constructs it with `template=` because it runs before the
cut-over, which is correct for its position in the sequence and is the reason
Task 3 Step 6 expects test churn. `_NoRowTemplates` is defined in Task 2
Step 1 and used in the same step.

**One known ordering hazard.** Task 2's test constructs `ShoppingRule` with
`template=`; after Task 3 that argument no longer exists. Task 3 Step 6
covers it — `tests/test_shopping.py` is in its test list, and the instruction
is to fix by deletion. If you execute Task 3 before Task 2, that test will
fail to construct; run them in order.
