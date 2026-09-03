# Menu Shopping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the bot navigate to the Workshop and Cards pages between runs and buy upgrades in priority order until nothing is affordable, rehearsing every decision in a dry run before it is ever allowed to spend a coin.

**Architecture:** A `ShoppingSession` state machine advances one step per scan inside the existing `run_once` loop — one capture, one decision, at most one tap — so pause, the frame stream and the event feed all keep working during a visit. Page recognition reuses the already-calibrated `config.PAGE_ANCHORS`. Purchase decisions gate on `coins >= price`, both read with the existing digit reader against a new `header` glyph atlas.

**Tech Stack:** Python 3.12, OpenCV (`cv2.matchTemplate`, `TM_CCOEFF_NORMED`), adbutils, FastAPI, SQLite, Next.js 15 static export, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-03-menu-shopping-design.md`

## Global Constraints

- **Emulator resolution is 1080x2400** (`config.EXPECTED_RESOLUTION`). Every template and region here was measured at that size; `cv2.matchTemplate` is not scale invariant.
- **No fixed coordinates. Ever.** Every tap point is derived from a matched template's position. The menu layout is dynamic — rows appear as unlocks are bought — so a hardcoded y is wrong the first time the bot succeeds.
- **Every digit read is all-or-nothing.** A partial recognition returns `None`, never a plausible wrong number. `digits.NumberReader.read` already enforces this; do not weaken it.
- **`publish()` never blocks.** Sinks offer into bounded queues and drop on overflow. Never await a sink.
- **One settings snapshot per scan.** `run_once` calls `self.controls.snapshot()` exactly once at the top and reads everything from it.
- **Templates must live inside `config.TEMPLATE_DIR`.** `Strategy.validated()` enforces containment; new row types must go through the same check.
- **Defaults must reproduce current behaviour.** A `strategies/*.json` with no `shopping` key must load and behave exactly as it does today.
- **Never run the whole test suite.** Run only the test files this plan names, e.g. `uv run pytest tests/test_pages.py -q`.
- **Do not commit anything that can spend a coin until Task 10.** Tasks 1–9 leave `armed` false and the tap path unreachable.

## Measured values (do not re-derive)

Taken from the committed fixtures on 2026-09-03. These are inputs, not guesses.

| Page | Fixture | Anchor top-left |
|---|---|---|
| `MAIN_MENU` | `tests/fixtures/menu_main.png` | (336, 360) |
| `WORKSHOP` | `tests/fixtures/menu_workshop_attack.png` | (32, 244) |
| `CARDS` | `tests/fixtures/menu_cards.png` | (32, 248) |

Header numbers, absolute on any menu page: coins at `(x=85, y=152, w=170, h=68)`, gems at `(x=430, y=152, w=190, h=68)`.

Header binarize threshold: **200**. At the default 140 the two `7`s in `1.77K` merge into one span and the read fails. Measured plateau where segmentation is correct: 170–240.

## File Structure

**New:**
- `pages.py` — menu page classification over `config.PAGE_ANCHORS`. Mirrors `screens.py`; deliberately separate from `ScreenState`.
- `shopping.py` — `ShoppingSession`, the step-per-scan state machine, plus `ShoppingPlan` (the ordered, category-grouped view of the strategy's rows).
- `tests/test_pages.py`, `tests/test_shopping.py`, `tests/test_shopping_templates.py`, `tests/test_header_digits.py`
- `web/ui/app/guide/page.tsx` — the community strategy guide.
- `web/ui/components/ShoppingEditor.tsx` — the Strategy page's Shopping section.

**Modified:**
- `config.py` — header regions, per-class binarize thresholds, workshop/cards templates, category tabs.
- `digits.py` — per-size-class binarize threshold; `header` known to the tooling but not to the affordability gate.
- `strategy.py` — `ShoppingRule`, `CardPolicy`, `Shopping`, and `Strategy.shopping`.
- `events.py` — five new event types.
- `tower_bot.py` — build and drive the session; the three suppressions.
- `build_atlas.py`, `tools/label_glyphs.py` — the `header` size class.
- `web/ui/components/Sidebar.tsx`, `web/ui/app/strategy/page.tsx`, `web/ui/lib/types.ts`
- `README.md`

---

### Task 1: Per-size-class binarize threshold

The header renders white text on a light purple panel. At the default threshold the panel bleeds into the glyphs and adjacent digits merge, so no header number can ever be read. This is a prerequisite for every other task.

**Files:**
- Modify: `config.py`
- Modify: `digits.py:212-250` (`NumberReader.read`)
- Test: `tests/test_header_digits.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config.DIGIT_BINARY_THRESHOLDS: dict[str, int]`; `digits.threshold_for(size_class: str) -> int`; `NumberReader.read` now binarizes with the class's own threshold.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_header_digits.py
"""The menu header, against committed captures.

The header is white text on a LIGHT panel, unlike every other number the bot
reads, which is light text on a dark one. At the shared default threshold the
panel survives binarisation and glues adjacent glyphs together: "1.77K"
segments as 1 . 77 K, and a merged span matches nothing, so the read fails.
"""

from pathlib import Path

import cv2
import pytest

import config
import digits

FIXTURES = Path(__file__).parent / "fixtures"
COINS = (85, 152, 170, 68)


def header_patch(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    x, y, w, h = COINS
    return img[y : y + h, x : x + w]


def test_default_threshold_merges_adjacent_glyphs() -> None:
    """Why this task exists. If this ever starts passing at 140, the font
    changed and the header threshold should be re-measured, not deleted."""
    patch = header_patch("menu_workshop_attack")
    spans = digits.glyph_spans(digits.binarize(patch, 140))
    assert len(spans) == 4, "expected the two 7s of 1.77K to merge at 140"


def test_header_threshold_separates_every_glyph() -> None:
    patch = header_patch("menu_workshop_attack")
    threshold = digits.threshold_for("header")
    spans = digits.glyph_spans(digits.binarize(patch, threshold))
    assert len(spans) == 5, f"1.77K must split into 1 . 7 7 K, got {len(spans)}"


def test_unknown_size_class_gets_the_shared_default() -> None:
    assert digits.threshold_for("price") == config.DIGIT_BINARY_THRESHOLD
    assert digits.threshold_for("nonsense") == config.DIGIT_BINARY_THRESHOLD


def test_header_threshold_sits_inside_the_measured_plateau() -> None:
    """170-240 all segment correctly; the default is the middle of that, not
    an edge, so a slightly different capture does not fall off it."""
    assert 170 <= digits.threshold_for("header") <= 240
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_header_digits.py -q`
Expected: FAIL with `AttributeError: module 'digits' has no attribute 'threshold_for'`

- [ ] **Step 3: Write minimal implementation**

In `config.py`, directly below `DIGIT_BINARY_THRESHOLD`:

```python
# Per-size-class overrides. The in-run and modal numbers are light glyphs on
# a dark panel, which is what the 140 default is for. The menu header is the
# other way round - white text on a light purple bar - and at 140 the bar
# survives binarisation and bridges adjacent glyphs: "1.77K" segments as
# 1 . 77 K, and a merged span matches no atlas entry, so the whole read
# fails. Measured on menu_workshop_attack.png: 170-240 all segment correctly,
# so 200 sits in the middle of the plateau rather than on its edge.
DIGIT_BINARY_THRESHOLDS: dict[str, int] = {"header": 200}
```

In `digits.py`, after the `SIZE_CLASSES` definition:

```python
def threshold_for(size_class: str) -> int:
    """The binarisation threshold for one size class.

    A lookup with a default rather than a required entry: adding a size class
    should not mean remembering to add a threshold, and every class but the
    header wants the shared one.
    """
    return config.DIGIT_BINARY_THRESHOLDS.get(
        size_class, config.DIGIT_BINARY_THRESHOLD
    )
```

In `digits.py`, inside `NumberReader.read`, replace `glyphs = split_glyphs(binarize(patch))` with:

```python
        glyphs = split_glyphs(binarize(patch, threshold_for(size_class)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_header_digits.py tests/test_digits.py -q`
Expected: PASS. `tests/test_digits.py` must stay green — the default path is unchanged.

- [ ] **Step 5: Commit**

```bash
git add config.py digits.py tests/test_header_digits.py
git commit -m "fix: binarise each size class at its own threshold

The menu header is white text on a light panel, the inverse of every number
the bot reads today. At the shared 140 the panel survives binarisation and
bridges glyphs: 1.77K segments as 1 . 77 K and the read fails outright.
Measured on the committed fixture, 170-240 all segment correctly, so the
header takes 200 and every other class keeps the default."
```

---

### Task 2: Header regions and the `header` size class

**Files:**
- Modify: `config.py`
- Modify: `digits.py` (add `ALL_SIZE_CLASSES`)
- Modify: `build_atlas.py:103-121` (`SOURCES`), `tools/label_glyphs.py:185`
- Test: `tests/test_header_digits.py`

**Interfaces:**
- Consumes: `digits.threshold_for` (Task 1).
- Produces: `config.HEADER_REGIONS: dict[str, tuple[Region, Region]]` mapping a page name to `(coins_region, gems_region)`; `digits.ALL_SIZE_CLASSES: tuple[str, ...]`.

**Why `SIZE_CLASSES` is not touched:** `tower_bot.build_affordability` requires *every* entry of `digits.SIZE_CLASSES` to have a built atlas or it downgrades the whole bot to brightness. Adding `header` there would break in-run digit affordability on every machine until someone harvested the header glyphs. `SIZE_CLASSES` stays the affordability gate; `ALL_SIZE_CLASSES` is what the harvesting tools offer.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_header_digits.py
import vision

PAGE_FIXTURES = {
    "MAIN_MENU": "menu_main",
    "WORKSHOP": "menu_workshop_attack",
    "CARDS": "menu_cards",
}
# What each fixture's header actually shows, counted by hand off the capture.
EXPECTED_GLYPHS = {
    "MAIN_MENU": (2, 1),   # "78" coins, "0" gems
    "WORKSHOP": (5, 2),    # "1.77K" coins, "40" gems
    "CARDS": (2, 2),       # "78" coins, "40" gems
}


@pytest.mark.parametrize("page,fixture", PAGE_FIXTURES.items())
def test_header_regions_land_on_the_numbers(page: str, fixture: str) -> None:
    """Anchored to each page's own anchor, not to absolute pixels - the three
    pages put their anchor in three different places."""
    img = cv2.imread(str(FIXTURES / f"{fixture}.png"), cv2.IMREAD_COLOR)
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    _, top_left = vision.best_score(img, cache.get(config.PAGE_ANCHORS[page]))

    coins_region, gems_region = config.HEADER_REGIONS[page]
    threshold = digits.threshold_for("header")
    expected_coins, expected_gems = EXPECTED_GLYPHS[page]

    for region, expected in ((coins_region, expected_coins), (gems_region, expected_gems)):
        patch = digits.crop(img, region, top_left)
        assert patch is not None, f"{page}: header region fell outside the frame"
        spans = digits.glyph_spans(digits.binarize(patch, threshold))
        assert len(spans) == expected, (
            f"{page}: expected {expected} glyphs, segmented {len(spans)}"
        )


def test_header_is_offered_to_the_atlas_tools_but_not_to_affordability() -> None:
    """build_affordability downgrades the WHOLE bot to brightness if any
    SIZE_CLASSES entry is unbuilt. The header must not be able to do that."""
    assert "header" in digits.ALL_SIZE_CLASSES
    assert "header" not in digits.SIZE_CLASSES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_header_digits.py -q -k "header_regions or atlas_tools"`
Expected: FAIL with `AttributeError: module 'config' has no attribute 'HEADER_REGIONS'`

- [ ] **Step 3: Write minimal implementation**

In `config.py`, after `PAGE_ANCHORS`:

```python
# The coin and gem counters in the menu header bar. Identical pixels on every
# menu page, but each page's ANCHOR sits somewhere different, so the offset
# is per page rather than one shared pair. Measured on the committed
# fixtures: MAIN_MENU anchors at (336, 360), WORKSHOP at (32, 244), CARDS at
# (32, 248), against a header at absolute (85, 152) and (430, 152).
#
# The boxes are wider than today's values need. The coin counter grows from
# "78" through "1.77K" to "1.23M" without moving its left edge, so the room
# has to be on the right, and a clipped glyph fails the whole read.
HEADER_REGIONS: dict[str, tuple[Region, Region]] = {
    "MAIN_MENU": (
        Region(dx=-251, dy=-208, w=170, h=68),
        Region(dx=94, dy=-208, w=190, h=68),
    ),
    "WORKSHOP": (
        Region(dx=53, dy=-92, w=170, h=68),
        Region(dx=398, dy=-92, w=190, h=68),
    ),
    "CARDS": (
        Region(dx=53, dy=-96, w=170, h=68),
        Region(dx=398, dy=-96, w=190, h=68),
    ),
}
```

In `digits.py`, directly under `SIZE_CLASSES`:

```python
# Every class the harvesting tools will offer. Deliberately NOT the same
# tuple as SIZE_CLASSES: tower_bot.build_affordability treats a missing
# SIZE_CLASSES atlas as "this machine cannot do digits at all" and downgrades
# the entire bot to brightness. The header only gates menu shopping, so an
# unbuilt header atlas must disable shopping and nothing else.
ALL_SIZE_CLASSES: tuple[str, ...] = SIZE_CLASSES + ("header",)
```

In `build_atlas.py`, add to `SOURCES` (the header renders identically on every
menu page, and `MAIN_MENU` is the only one the existing screen classifier can
recognise, so harvest from there):

```python
    # The header bar. Sampled from MAIN_MENU because that is the only menu
    # page screens.classify() knows - the workshop and cards pages read as
    # UNKNOWN to it by design - and the bar is pixel-identical on all three.
    "header": (
        Source("MAIN_MENU", config.HEADER_REGIONS["MAIN_MENU"][0]),
        Source("MAIN_MENU", config.HEADER_REGIONS["MAIN_MENU"][1]),
    ),
```

In `tools/label_glyphs.py:185`, change the choices:

```python
    parser.add_argument("--size-class", choices=digits.ALL_SIZE_CLASSES, required=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_header_digits.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add config.py digits.py build_atlas.py tools/label_glyphs.py tests/test_header_digits.py
git commit -m "feat: locate the coin and gem counters in the menu header

Per-page regions rather than one shared pair: the header pixels are identical
on all three menu pages but each page anchors somewhere different.

The header is offered to the atlas tools through a new ALL_SIZE_CLASSES and
deliberately kept out of SIZE_CLASSES, which build_affordability reads as
'can this machine do digits at all' - an unbuilt header atlas must disable
menu shopping, not downgrade the whole bot to brightness."
```

---

### Task 3: Harvest and label the header atlas

Not test-first: this produces image data, and the test is that the data reads.

**Files:**
- Create: `templates/atlas/header/*.png`
- Test: `tests/test_header_digits.py`

**Interfaces:**
- Consumes: Task 2's `SOURCES["header"]`.
- Produces: a labelled `templates/atlas/header/` containing at minimum `0`–`9`, `dot`, `K`, `M`, `B`.

- [ ] **Step 1: Harvest glyphs from the live emulator**

The coin balance has to *change* for all ten digits to appear, so harvest across a session rather than in one burst. Leave the game on the main menu and let runs complete between harvests.

```bash
export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools"
adb devices                     # expect emulator-5554
uv run build_atlas.py --size-class header --frames 20
```

- [ ] **Step 2: Auto-label against the wallet class**

The header and the wallet are the same font at different sizes, so the wallet class is a usable reference. `label_glyphs.py` scores and renames the confident ones and writes a contact sheet of the rest.

```bash
uv run tools/label_glyphs.py --size-class header --reference wallet
uv run tools/label_glyphs.py --size-class header --reference wallet --apply
```

- [ ] **Step 3: Hand-label what the contact sheet could not place**

`K`, `M`, `B` and `.` score low against the wallet class because the wallet class does not contain them — that is the README's documented gap and it is exactly what is being closed here. Rename them by hand off `templates/atlas/_sheet_header.png`:

```
templates/atlas/header/glyph_007.png  ->  templates/atlas/header/K.png
templates/atlas/header/glyph_011.png  ->  templates/atlas/header/dot.png
```

- [ ] **Step 4: Write the completeness test**

```python
# append to tests/test_header_digits.py
REQUIRED = set("0123456789") | {".", "K", "M", "B"}


def test_header_atlas_is_complete() -> None:
    """Shopping refuses to arm without every one of these, so an incomplete
    atlas should fail here rather than silently disabling the feature."""
    atlas = digits.AtlasCache().get("header")
    assert atlas is not None, "run build_atlas.py --size-class header"
    missing = REQUIRED - atlas.labels
    assert not missing, f"header atlas is missing {sorted(missing)}"


def test_header_reads_a_suffixed_balance() -> None:
    """The README's known gap, closed. 1.77K is the balance on the fixture."""
    img = cv2.imread(str(FIXTURES / "menu_workshop_attack.png"), cv2.IMREAD_COLOR)
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    _, top_left = vision.best_score(img, cache.get(config.PAGE_ANCHORS["WORKSHOP"]))
    reader = digits.NumberReader()
    coins_region, gems_region = config.HEADER_REGIONS["WORKSHOP"]
    assert reader.read(img, coins_region, top_left, "header") == 1770
    assert reader.read(img, gems_region, top_left, "header") == 40
```

- [ ] **Step 5: Run the test**

Run: `uv run pytest tests/test_header_digits.py -q`
Expected: PASS. If `test_header_atlas_is_complete` names missing digits, go back to step 1 and harvest across more of a session — a digit that never appeared in the balance cannot have been captured.

- [ ] **Step 6: Update the README's known-gap section**

In `README.md`, replace the "Known gap: no suffix glyphs" section with a note that the `header` class carries `K`, `M`, `B` and `.`, that the wallet and price classes still do not, and that a wallet rendered as `$1.5K` still falls back to brightness for that scan.

- [ ] **Step 7: Commit**

```bash
git add templates/atlas/header README.md tests/test_header_digits.py
git commit -m "feat: build the header glyph atlas, suffixes included

Closes the README's known gap for the one class that cannot work without it:
the coin balance renders as 1.77K, and a menu purchase gates on coins >=
price, so an unreadable balance means no purchase can ever be approved.

The wallet and price classes still have no suffix glyphs - that fallback is
unchanged and still documented."
```

---

### Task 4: Menu page classification

**Files:**
- Create: `pages.py`
- Test: `tests/test_pages.py`

**Interfaces:**
- Consumes: `config.PAGE_ANCHORS`, `vision.TemplateCache`, `vision.best_score`.
- Produces:
  - `pages.PageReading` — frozen dataclass with `page: str`, `confidence: float`, `scores: dict[str, float]`, `top_left: tuple[int, int] | None`.
  - `pages.UNKNOWN: str = "UNKNOWN"`
  - `pages.classify_page(screen: Image, cache: vision.TemplateCache, threshold: float = config.ANCHOR_THRESHOLD) -> PageReading`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pages.py
"""Menu page classification, against committed captures of each page.

Separate from screens.py on purpose. ScreenState models the RUN lifecycle,
and screens.classify() does ScreenState(winner), which raises on any name the
enum does not have. Menu pages are not run states and must never become enum
members; outside a shopping visit a menu page reading UNKNOWN is correct.
"""

from pathlib import Path

import cv2
import pytest

import config
import pages
import vision

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_FIXTURES = {
    "MAIN_MENU": "menu_main",
    "WORKSHOP": "menu_workshop_attack",
    "CARDS": "menu_cards",
    "MISSIONS": "menu_missions",
}


def frame(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    return img


@pytest.fixture
def cache():
    return vision.TemplateCache(config.TEMPLATE_DIR)


@pytest.mark.parametrize("page,fixture", PAGE_FIXTURES.items())
def test_each_page_classifies_as_itself(page: str, fixture: str, cache) -> None:
    reading = pages.classify_page(frame(fixture), cache)
    assert reading.page == page
    assert reading.confidence >= 0.9
    assert reading.top_left is not None


@pytest.mark.parametrize("page,fixture", PAGE_FIXTURES.items())
def test_no_page_is_confused_for_another(page: str, fixture: str, cache) -> None:
    """Measured worst-case separation is 0.592, so 0.75 leaves real headroom
    rather than tracking the current number."""
    reading = pages.classify_page(frame(fixture), cache)
    others = {name: score for name, score in reading.scores.items() if name != page}
    assert max(others.values()) < 0.75, f"{page} was nearly confused: {others}"


def test_an_in_run_frame_is_not_a_menu_page(cache) -> None:
    """The gate that keeps a shopping visit from starting mid-fight."""
    reading = pages.classify_page(frame("in_run_lit"), cache)
    assert reading.page == pages.UNKNOWN
    assert reading.top_left is None


def test_the_death_modal_is_not_a_menu_page(cache) -> None:
    reading = pages.classify_page(frame("game_over"), cache)
    assert reading.page == pages.UNKNOWN


def test_page_names_never_leak_into_the_run_state_enum() -> None:
    """Regression guard for the exact crash config.py warns about."""
    import screens

    menu_only = set(config.PAGE_ANCHORS) - set(config.SCREEN_ANCHORS)
    assert menu_only, "expected menu pages the run enum does not model"
    for name in menu_only:
        with pytest.raises(ValueError):
            screens.ScreenState(name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pages.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pages'`

- [ ] **Step 3: Write minimal implementation**

```python
# pages.py
"""Which MENU page is on screen?

Deliberately not screens.py. That module models the run lifecycle as an enum
and does ScreenState(winner), which raises on any name the enum lacks - so a
menu page can never become a member of it without turning every workshop
frame into a crash. config.py has said so since the anchors were cut; this is
the module that makes use of them without breaking that rule.

Only consulted while a shopping visit is live. Outside one, a menu page
reading UNKNOWN to the screen tracker is correct behaviour, not a gap.
"""

from __future__ import annotations

from dataclasses import dataclass

import config
import vision
from device import Image

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PageReading:
    """One frame's page classification, with every anchor's score.

    `scores` is kept for the same reason screens.ScreenReading keeps it: when
    a page stops being recognised, the useful question is what it nearly was.
    """

    page: str
    confidence: float
    scores: dict[str, float]
    top_left: tuple[int, int] | None = None


def classify_page(
    screen: Image,
    cache: vision.TemplateCache,
    threshold: float = config.ANCHOR_THRESHOLD,
) -> PageReading:
    """Best-scoring menu page, or UNKNOWN below threshold.

    Full-frame against every anchor, matching screens.classify(): four
    templates at ~130ms against a 2s scan interval is not worth optimising,
    and an ROI would need retuning every time the header grows a row.
    """
    scores: dict[str, float] = {}
    positions: dict[str, tuple[int, int]] = {}

    for name, template_path in config.PAGE_ANCHORS.items():
        score, top_left = vision.best_score(screen, cache.get(template_path))
        scores[name] = score
        positions[name] = top_left

    winner = max(scores, key=lambda name: scores[name])
    confidence = scores[winner]

    if confidence < threshold:
        return PageReading(UNKNOWN, confidence, scores)

    return PageReading(winner, confidence, scores, top_left=positions[winner])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pages.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add pages.py tests/test_pages.py
git commit -m "feat: classify which menu page is on screen

Uses the PAGE_ANCHORS cut and tested months ago and never wired to anything.
Kept out of screens.py because ScreenState(winner) raises on a name the enum
lacks - a test pins that, so nobody 'simplifies' the two modules together."
```

---

### Task 5: Workshop and Cards templates

Not test-first for the cutting itself; the goldens are the test.

**Files:**
- Create: `templates/workshop/*.png`, `templates/cards/*.png`
- Modify: `config.py`
- Test: `tests/test_shopping_templates.py`

**Interfaces:**
- Produces: `config.WORKSHOP_TABS: dict[str, str]`, `config.WORKSHOP_ROWS: dict[str, str]`, `config.CARD_BUTTONS: dict[str, str]`, `config.ROW_PRICE_REGION: Region`.

**Cutting rule — read this before cropping anything.** Cut each row template from the **tile's top-left corner**, a fixed-size crop that includes the tile border and the label text, *not* a tight crop of the words. Two rows on the same page have labels on different numbers of lines ("Damage" is one line, "Attack Speed" is two), so tight crops put the match `top_left` at a different height inside each tile — and then one price offset cannot serve every row. Anchoring on the tile corner makes the offset constant, which is the same problem `config.buy_point()` solves in-run and the same way.

- [ ] **Step 1: Capture the pages**

Already committed for Attack, Defense and Utility (`tests/fixtures/menu_workshop_*.png`) and Cards (`tests/fixtures/menu_cards.png`). Recapture only if the layout has changed:

```bash
export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools"
uv run grab_screen.py /tmp/ws.png
```

- [ ] **Step 2: Cut the templates**

From `tests/fixtures/menu_workshop_attack.png`:
- `templates/workshop/row_damage.png` — tile corner crop, ~200x160 from the left tile's top-left
- `templates/workshop/row_attack_speed.png` — same, right tile
- `templates/workshop/row_critical_chance.png`, `row_critical_factor.png` — second row
- `templates/workshop/unlock_range_upgrades.png` — the full-width unlock tile

From `menu_workshop_defense.png`: `row_health.png`, `row_health_regen.png`, `unlock_defense_upgrades.png`.
From `menu_workshop_utility.png`: `unlock_cash_bonuses.png`.
From any workshop capture: `tab_attack.png`, `tab_defense.png`, `tab_utility.png` — the coloured category tabs above the bottom nav bar. Cut each from a frame where that tab is **not** selected as well as one where it is, and keep the unselected crop: a selected tab is brighter, and a template cut lit will match its own selected state and nothing else.
From `menu_cards.png`: `cards/buy_x1.png`, `cards/buy_x10.png`.

Preview each crop and get a paste-ready `Region` with the documented tool:

```bash
uv run tools/crop_preview.py tests/fixtures/menu_workshop_attack.png \
    --rect 29 476 200 160 --out /tmp/crop.png
```

- [ ] **Step 3: Measure the price offset**

With `row_damage.png` cut, find where it matches and where that row's price box sits relative to the match. Inset the box from the border so a theme change cannot smear it.

```bash
uv run tools/crop_preview.py tests/fixtures/menu_workshop_attack.png \
    --rect <price box> --out /tmp/price.png
```

Write the result into `config.py` as `ROW_PRICE_REGION`. Expect roughly `dx≈330, dy≈135, w≈150, h≈40` — verify, do not paste.

- [ ] **Step 4: Add the config entries**

```python
# --- Menu shopping --------------------------------------------------------
# The workshop's category tabs. Cut UNSELECTED: a selected tab is brighter,
# and a template cropped lit matches only its own selected state.
WORKSHOP_TABS: dict[str, str] = {
    "ATTACK": "workshop/tab_attack.png",
    "DEFENSE": "workshop/tab_defense.png",
    "UTILITY": "workshop/tab_utility.png",
}

# Every buyable row this account can currently see. Rows behind an unlock
# tile are absent on purpose: a template that was never cut against a real
# frame matches nothing or matches everything, and there is no third option.
# Add them when the unlock reveals them and the crop is taken from a live
# capture.
WORKSHOP_ROWS: dict[str, str] = {
    "Unlock Cash Bonuses": "workshop/unlock_cash_bonuses.png",
    "Unlock Defense Upgrades": "workshop/unlock_defense_upgrades.png",
    "Unlock Range Upgrades": "workshop/unlock_range_upgrades.png",
    "Health": "workshop/row_health.png",
    "Health Regen": "workshop/row_health_regen.png",
    "Damage": "workshop/row_damage.png",
    "Attack Speed": "workshop/row_attack_speed.png",
    "Critical Chance": "workshop/row_critical_chance.png",
    "Critical Factor": "workshop/row_critical_factor.png",
}

CARD_BUTTONS: dict[str, str] = {
    "x1": "cards/buy_x1.png",
    "x10": "cards/buy_x10.png",
}

# The price strip inside a workshop row, from that ROW's own matched
# template - not from the page anchor, because each row has its own box.
# Row templates are cut from the tile's top-left CORNER rather than tight to
# the label precisely so this one offset lands on every row: "Damage" is one
# line and "Attack Speed" is two, so tight crops would put the match origin
# at a different height in each tile and need an offset apiece.
ROW_PRICE_REGION: Region = Region(dx=330, dy=135, w=150, h=40)
```

- [ ] **Step 5: Write the golden test**

```python
# tests/test_shopping_templates.py
"""Workshop and cards templates, against committed captures.

Golden data. A failure here means a template was re-cut badly or the game's
UI moved - both of which send taps to the wrong place, on a page where a tap
spends coins.
"""

from pathlib import Path

import cv2
import pytest

import config
import digits
import vision

FIXTURES = Path(__file__).parent / "fixtures"
ROWS_BY_FIXTURE = {
    "menu_workshop_attack": [
        "Damage", "Attack Speed", "Critical Chance", "Critical Factor",
        "Unlock Range Upgrades",
    ],
    "menu_workshop_defense": ["Health", "Health Regen", "Unlock Defense Upgrades"],
    "menu_workshop_utility": ["Unlock Cash Bonuses"],
}
# What each row's price box reads on its fixture, off the capture by hand.
PRICES = {
    "Damage": 30, "Attack Speed": 30,
    "Critical Chance": 50, "Critical Factor": 50,
    "Unlock Range Upgrades": 50,
    "Health": 30, "Health Regen": 30, "Unlock Defense Upgrades": 75,
    "Unlock Cash Bonuses": 40,
}


def frame(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    return img


@pytest.fixture
def cache():
    return vision.TemplateCache(config.TEMPLATE_DIR)


@pytest.mark.parametrize("fixture,rows", ROWS_BY_FIXTURE.items())
def test_every_row_is_found_on_its_own_tab(fixture: str, rows, cache) -> None:
    screen = frame(fixture)
    for name in rows:
        match = vision.locate_template(screen, cache.get(config.WORKSHOP_ROWS[name]), 0.9)
        assert match is not None, f"{name} not found on {fixture}"


@pytest.mark.parametrize("fixture,rows", ROWS_BY_FIXTURE.items())
def test_rows_are_absent_from_the_other_tabs(fixture: str, rows, cache) -> None:
    """A row matching on a tab it does not live on would be bought there."""
    screen = frame(fixture)
    foreign = set(config.WORKSHOP_ROWS) - set(rows)
    for name in foreign:
        score, _ = vision.best_score(screen, cache.get(config.WORKSHOP_ROWS[name]))
        assert score < 0.9, f"{name} scored {score:.3f} on {fixture}, where it is absent"


@pytest.mark.parametrize("fixture,rows", ROWS_BY_FIXTURE.items())
def test_the_price_offset_lands_on_every_rows_price(fixture: str, rows, cache) -> None:
    """One offset for every row is the whole reason templates are cut from the
    tile corner. If this fails for one row, that row was cut tight to its text."""
    screen = frame(fixture)
    reader = digits.NumberReader()
    for name in rows:
        match = vision.locate_template(screen, cache.get(config.WORKSHOP_ROWS[name]), 0.9)
        assert match is not None, f"{name} not found on {fixture}"
        price = reader.read(screen, config.ROW_PRICE_REGION, match.top_left, "price")
        assert price == PRICES[name], f"{name}: read {price}, expected {PRICES[name]}"


def test_both_card_buttons_are_found(cache) -> None:
    screen = frame("menu_cards")
    for name in ("x1", "x10"):
        match = vision.locate_template(screen, cache.get(config.CARD_BUTTONS[name]), 0.9)
        assert match is not None, f"card button {name} not found"


def test_the_unaffordable_card_button_is_visibly_dimmer(cache) -> None:
    """The greyed-out state the README admits was never captured. On this
    fixture 40 gems buys x1 (20) and not x10 (200), so both states are on one
    frame - and TM_CCOEFF_NORMED is blind to the difference, which is exactly
    why the brightness check exists."""
    screen = frame("menu_cards")
    ratios = {}
    for name in ("x1", "x10"):
        template = cache.get(config.CARD_BUTTONS[name])
        match = vision.locate_template(screen, template, 0.9)
        ratios[name] = vision.brightness_ratio(screen, match, template)
    assert ratios["x10"] < ratios["x1"], f"no separation measured: {ratios}"


@pytest.mark.parametrize("tab", ["ATTACK", "DEFENSE", "UTILITY"])
def test_every_category_tab_is_on_every_workshop_page(tab: str, cache) -> None:
    """The tab bar does not change between categories, so any workshop
    capture must carry all three - that is what makes switching tabs possible
    from wherever the visit currently is."""
    for fixture in ROWS_BY_FIXTURE:
        match = vision.locate_template(frame(fixture), cache.get(config.WORKSHOP_TABS[tab]), 0.8)
        assert match is not None, f"{tab} tab not found on {fixture}"


def test_every_configured_template_exists_on_disk(cache) -> None:
    for path in (*config.WORKSHOP_TABS.values(), *config.WORKSHOP_ROWS.values(),
                 *config.CARD_BUTTONS.values()):
        assert cache.get(path) is not None, f"missing template: {path}"
```

- [ ] **Step 6: Run the test and iterate on the crops**

Run: `uv run pytest tests/test_shopping_templates.py -q`
Expected: PASS. `test_the_price_offset_lands_on_every_rows_price` is the one that will fail first; when it does, the named row was cut tight to its text instead of to the tile corner. Re-cut it, do not add a second offset.

Record the measured numbers from `test_the_unaffordable_card_button_is_visibly_dimmer` in a comment beside `DEFAULT_BRIGHTNESS_RATIO` in `config.py`, and correct the 0.75 default if the measurement says it is wrong.

- [ ] **Step 7: Commit**

```bash
git add templates/workshop templates/cards config.py tests/test_shopping_templates.py
git commit -m "feat: cut workshop and cards templates, with goldens

Row templates are cut from each tile's top-left CORNER, not tight to the
label: 'Damage' is one line and 'Attack Speed' is two, so tight crops put the
match origin at a different height in each tile and no single price offset
could serve them. A test proves one offset reads every row's price.

Also finally measures the greyed-out unaffordable state the README says was
never captured - the cards page shows x1 lit and x10 greyed on one frame."
```

---

### Task 6: The `shopping` strategy schema

**Files:**
- Modify: `strategy.py`
- Test: `tests/test_strategy_shopping.py`

**Interfaces:**
- Consumes: `strategy.ControlError`, `_check_types`, `_own_values`, `_in_range`.
- Produces:
  - `strategy.CATEGORIES: tuple[str, ...] = ("ATTACK", "DEFENSE", "UTILITY")`
  - `strategy.CARD_BATCHES: tuple[str, ...] = ("x1", "x10")`
  - `strategy.ShoppingRule` — frozen: `name: str`, `template: str`, `category: str`, `enabled: bool = True`, `threshold: float = 0.9`, `brightness_ratio: float = config.DEFAULT_BRIGHTNESS_RATIO`; method `as_action() -> config.Action`
  - `strategy.CardPolicy` — frozen: `enabled: bool = False`, `gem_floor: int = 40`, `max_per_visit: int = 2`, `batch: str = "x1"`
  - `strategy.Shopping` — frozen: `enabled: bool = False`, `armed: bool = False`, `visit_every_n_runs: int = 1`, `max_taps_per_visit: int = 40`, `workshop: tuple[ShoppingRule, ...] = ()`, `cards: CardPolicy = CardPolicy()`; methods `to_dict()`, `categories_in_priority_order() -> tuple[str, ...]`, `rows_for(category: str) -> tuple[ShoppingRule, ...]`
  - `strategy.Shopping.from_dict(raw) -> Shopping`
  - `Strategy.shopping: Shopping` field, included in `to_dict()`/`from_dict()`, and `"shopping"` added to `PATCHABLE_FIELDS`
  - `Strategy.validated()` also checks `shopping.workshop` template containment

- [ ] **Step 1: Write the failing test**

```python
# tests/test_strategy_shopping.py
"""The shopping half of a strategy.

Everything here is a value the browser can POST, so every field is typed and
range-checked the way ActionRule's are. `enabled="no"` is truthy, and a
shopping policy that spends coins on a truthy string is a real bug, not a
validation nicety.
"""

import dataclasses

import pytest

import config
import strategy as strategy_mod
from strategy import CardPolicy, ControlError, Shopping, ShoppingRule, Strategy


def a_rule(**over):
    base = dict(name="Damage", template="workshop/row_damage.png", category="ATTACK")
    return ShoppingRule(**{**base, **over})


# -- defaults preserve today's behaviour -----------------------------------
def test_a_strategy_with_no_shopping_key_loads_and_buys_nothing() -> None:
    raw = Strategy.from_config().to_dict()
    del raw["shopping"]
    loaded = Strategy.from_dict(raw)
    assert loaded.shopping.enabled is False
    assert loaded.shopping.armed is False


def test_shopping_round_trips_through_to_dict() -> None:
    original = Strategy.from_config()
    assert Strategy.from_dict(original.to_dict()) == original


# -- the arm switch --------------------------------------------------------
def test_enabled_and_armed_are_independent() -> None:
    """enabled=True, armed=False is the rehearsal, and it must be expressible."""
    rehearsal = Shopping(enabled=True, armed=False, workshop=(a_rule(),))
    assert rehearsal.enabled and not rehearsal.armed


@pytest.mark.parametrize("field", ["enabled", "armed"])
def test_a_truthy_string_is_not_a_switch(field: str) -> None:
    with pytest.raises(ControlError) as exc:
        Shopping(**{field: "no"})
    assert exc.value.field == field


# -- rows ------------------------------------------------------------------
def test_a_row_needs_a_known_category() -> None:
    with pytest.raises(ControlError) as exc:
        a_rule(category="ULTIMATE")
    assert exc.value.field == "category"


def test_row_names_must_be_unique() -> None:
    with pytest.raises(ControlError) as exc:
        Shopping(workshop=(a_rule(), a_rule()))
    assert exc.value.field == "workshop"


def test_a_row_threshold_of_zero_is_refused() -> None:
    """Zero matches anything, so the bot would tap wherever the template
    happened to correlate best - on a page where a tap spends coins."""
    with pytest.raises(ControlError):
        a_rule(threshold=0.0)


def test_a_row_converts_to_the_shape_the_matcher_takes() -> None:
    action = a_rule(threshold=0.95).as_action()
    assert isinstance(action, config.Action)
    assert action.name == "Damage"
    assert action.threshold == 0.95


# -- category order is derived, not hardcoded ------------------------------
def test_categories_come_out_in_first_appearance_order() -> None:
    shopping = Shopping(workshop=(
        a_rule(name="Unlock Cash Bonuses", category="UTILITY"),
        a_rule(name="Health", category="DEFENSE"),
        a_rule(name="Damage", category="ATTACK"),
        a_rule(name="Health Regen", category="DEFENSE"),
    ))
    assert shopping.categories_in_priority_order() == ("UTILITY", "DEFENSE", "ATTACK")


def test_reordering_rows_reorders_the_tabs_visited() -> None:
    shopping = Shopping(workshop=(
        a_rule(name="Damage", category="ATTACK"),
        a_rule(name="Health", category="DEFENSE"),
    ))
    assert shopping.categories_in_priority_order()[0] == "ATTACK"


def test_disabled_rows_do_not_pull_in_their_category() -> None:
    """Visiting a tab whose every row is switched off is wasted taps."""
    shopping = Shopping(workshop=(
        a_rule(name="Damage", category="ATTACK", enabled=False),
        a_rule(name="Health", category="DEFENSE"),
    ))
    assert shopping.categories_in_priority_order() == ("DEFENSE",)


def test_rows_for_a_category_keeps_priority_order_and_drops_disabled() -> None:
    shopping = Shopping(workshop=(
        a_rule(name="Damage", category="ATTACK"),
        a_rule(name="Attack Speed", category="ATTACK", enabled=False),
        a_rule(name="Critical Chance", category="ATTACK"),
    ))
    assert [r.name for r in shopping.rows_for("ATTACK")] == ["Damage", "Critical Chance"]


# -- cards -----------------------------------------------------------------
def test_the_gem_floor_may_not_be_negative() -> None:
    with pytest.raises(ControlError) as exc:
        CardPolicy(gem_floor=-1)
    assert exc.value.field == "gem_floor"


def test_a_gem_floor_of_zero_is_legal() -> None:
    """Spend every gem is a real, if bold, choice - unlike a negative floor,
    which is not a choice at all."""
    assert CardPolicy(gem_floor=0).gem_floor == 0


def test_the_batch_size_must_be_one_the_game_offers() -> None:
    with pytest.raises(ControlError) as exc:
        CardPolicy(batch="x100")
    assert exc.value.field == "batch"


def test_a_visit_must_buy_at_least_one_card_when_it_buys_any() -> None:
    with pytest.raises(ControlError):
        CardPolicy(max_per_visit=0)


# -- limits ----------------------------------------------------------------
def test_a_visit_needs_a_tap_budget() -> None:
    with pytest.raises(ControlError) as exc:
        Shopping(max_taps_per_visit=0)
    assert exc.value.field == "max_taps_per_visit"


def test_visit_cadence_is_at_least_every_run() -> None:
    with pytest.raises(ControlError):
        Shopping(visit_every_n_runs=0)


# -- patching --------------------------------------------------------------
def test_shopping_can_be_patched_through_merged() -> None:
    before = Strategy.from_config()
    after = before.merged({"shopping": {"enabled": True, "armed": False}})
    assert after.shopping.enabled is True
    assert before.shopping.enabled is False, "merged() must not mutate"


def test_an_invalid_patch_leaves_the_original_untouched() -> None:
    before = Strategy.from_config()
    with pytest.raises(ControlError):
        before.merged({"shopping": {"cards": {"batch": "x100"}}})
    assert before.shopping.cards.batch == "x1"


def test_an_unknown_shopping_field_is_refused() -> None:
    with pytest.raises(ControlError) as exc:
        Shopping.from_dict({"enabled": True, "spend_everything": True})
    assert exc.value.field == "spend_everything"


# -- template containment --------------------------------------------------
def test_a_shopping_row_may_not_escape_the_template_directory(tmp_path) -> None:
    """Same suspicion actions get: a template arrives in client JSON and is
    joined to a path, so it must name a file INSIDE the template dir."""
    base = Strategy.from_config()
    escaped = dataclasses.replace(
        base,
        shopping=dataclasses.replace(
            base.shopping, workshop=(a_rule(template="../../etc/passwd"),)
        ),
    )
    with pytest.raises(ControlError) as exc:
        escaped.validated()
    assert exc.value.field == "template"


def test_a_disabled_shopping_row_is_still_checked() -> None:
    """A disabled row is one checkbox away from spending coins."""
    base = Strategy.from_config()
    broken = dataclasses.replace(
        base,
        shopping=dataclasses.replace(
            base.shopping,
            workshop=(a_rule(template="workshop/nope.png", enabled=False),),
        ),
    )
    with pytest.raises(ControlError):
        broken.validated()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_shopping.py -q`
Expected: FAIL with `ImportError: cannot import name 'CardPolicy' from 'strategy'`

- [ ] **Step 3: Write minimal implementation**

Add to `strategy.py`, above `Strategy`:

```python
CATEGORIES: tuple[str, ...] = ("ATTACK", "DEFENSE", "UTILITY")
CARD_BATCHES: tuple[str, ...] = ("x1", "x10")

# A menu row is matched on a page where a tap spends coins, so it defaults
# stricter than an in-run action: 0.9, not DEFAULT_THRESHOLD.
DEFAULT_ROW_THRESHOLD: float = 0.9
MAX_TAPS_PER_VISIT = 200
MAX_CARDS_PER_VISIT = 50

_SHOPPING_RULE_TYPES: dict[str, tuple[type, ...]] = {
    "name": (str,), "template": (str,), "category": (str,),
    "enabled": (bool,), "threshold": (int, float), "brightness_ratio": (int, float),
}
_CARD_TYPES: dict[str, tuple[type, ...]] = {
    "enabled": (bool,), "gem_floor": (int,),
    "max_per_visit": (int,), "batch": (str,),
}
_SHOPPING_TYPES: dict[str, tuple[type, ...]] = {
    "enabled": (bool,), "armed": (bool,),
    "visit_every_n_runs": (int,), "max_taps_per_visit": (int,),
}


@dataclass(frozen=True)
class ShoppingRule:
    """One menu row the bot may buy, and how sure it must be first.

    Mirrors ActionRule, plus `category` - which tab the row lives on. The
    category is not derivable from the template path: a row can be moved
    between tabs by a game update, and a wrong tab means the bot searches a
    page the row is not on and silently buys nothing.
    """

    name: str
    template: str
    category: str
    enabled: bool = True
    threshold: float = DEFAULT_ROW_THRESHOLD
    brightness_ratio: float = config.DEFAULT_BRIGHTNESS_RATIO

    def __post_init__(self) -> None:
        _check_types(_own_values(self), _SHOPPING_RULE_TYPES)
        if not self.name:
            raise ControlError("name", "a shopping row needs a name")
        if not self.template:
            raise ControlError("template", f"{self.name} needs a template file")
        if self.category not in CATEGORIES:
            raise ControlError("category", f"category must be one of {CATEGORIES}")
        if not 0.0 < self.threshold <= 1.0:
            raise ControlError("threshold", "threshold must be above 0 and at most 1")
        _in_range("brightness_ratio", self.brightness_ratio, 0.0, 1.0)

    def as_action(self) -> config.Action:
        return config.Action(
            name=self.name,
            template=self.template,
            threshold=self.threshold,
            brightness_ratio=self.brightness_ratio,
        )


@dataclass(frozen=True)
class CardPolicy:
    """How many gems the bot may turn into cards, and where it must stop.

    `gem_floor` is inclusive-at-zero on purpose: spending down to nothing is
    a real choice, unlike a negative floor, which is not a choice at all.
    """

    enabled: bool = False
    gem_floor: int = 40
    max_per_visit: int = 2
    batch: str = "x1"

    def __post_init__(self) -> None:
        _check_types(_own_values(self), _CARD_TYPES)
        if self.gem_floor < 0:
            raise ControlError("gem_floor", "gem_floor may not be negative")
        _in_range("max_per_visit", self.max_per_visit, 1, MAX_CARDS_PER_VISIT)
        if self.batch not in CARD_BATCHES:
            raise ControlError("batch", f"batch must be one of {CARD_BATCHES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "gem_floor": self.gem_floor,
            "max_per_visit": self.max_per_visit, "batch": self.batch,
        }


@dataclass(frozen=True)
class Shopping:
    """The between-runs spending policy.

    `enabled` and `armed` are two switches, not one mode. enabled+unarmed is
    the rehearsal: navigate, read, decide, publish, tap nothing. `armed` is
    the only field in this whole config that stands between a miscalibrated
    template and a currency you cannot get back, so it is its own boolean
    rather than a value of something else.

    `workshop` order IS priority, matching Strategy.actions - and the order
    the CATEGORIES are visited in is derived from it rather than fixed, so
    reordering rows is the only control anyone needs.
    """

    enabled: bool = False
    armed: bool = False
    visit_every_n_runs: int = 1
    max_taps_per_visit: int = 40
    workshop: tuple[ShoppingRule, ...] = ()
    cards: CardPolicy = CardPolicy()

    def __post_init__(self) -> None:
        object.__setattr__(self, "workshop", tuple(self.workshop))
        _check_types(_own_values(self), _SHOPPING_TYPES)
        names = [rule.name for rule in self.workshop]
        if len(set(names)) != len(names):
            raise ControlError("workshop", "shopping row names must be unique")
        _in_range("visit_every_n_runs", self.visit_every_n_runs, 1, 100)
        _in_range("max_taps_per_visit", self.max_taps_per_visit, 1, MAX_TAPS_PER_VISIT)

    def rows_for(self, category: str) -> tuple[ShoppingRule, ...]:
        """Enabled rows on one tab, still in priority order."""
        return tuple(
            rule for rule in self.workshop
            if rule.category == category and rule.enabled
        )

    def categories_in_priority_order(self) -> tuple[str, ...]:
        """Which tabs to visit, in the order the row list implies.

        Derived rather than fixed. Only one tab is readable at a time, so
        honouring a global priority order literally would mean re-checking
        every tab after every purchase - thrashing tabs and burning the tap
        budget on navigation. Visiting each tab once, in the order its
        highest-priority row appears, spends in the intended order and costs
        two tab taps.

        Tabs whose every row is disabled are skipped: visiting one is taps
        spent to read a page nothing will be bought from.
        """
        seen: list[str] = []
        for rule in self.workshop:
            if rule.enabled and rule.category not in seen:
                seen.append(rule.category)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "armed": self.armed,
            "visit_every_n_runs": self.visit_every_n_runs,
            "max_taps_per_visit": self.max_taps_per_visit,
            "workshop": [
                {
                    "name": r.name, "template": r.template, "category": r.category,
                    "enabled": r.enabled, "threshold": r.threshold,
                    "brightness_ratio": r.brightness_ratio,
                }
                for r in self.workshop
            ],
            "cards": self.cards.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Shopping:
        """Parse a stored or posted shopping policy, naming what it got wrong.

        The one place a raw shopping dict is parsed, used by both
        Strategy.from_dict() and Strategy.merged() - the same rule
        _parse_action_rows follows, and for the same reason: two parsers is
        how "unknown field" comes to mean two different things.
        """
        if not isinstance(raw, Mapping):
            raise ControlError(
                "shopping", f"shopping must be a mapping, not {type(raw).__name__!r}"
            )
        known = {f.name for f in dataclasses.fields(cls)}
        for key in raw:
            if key not in known:
                raise ControlError(key, f"unknown shopping field {key!r}")

        values = {k: v for k, v in raw.items() if k not in ("workshop", "cards")}
        if "workshop" in raw:
            values["workshop"] = _parse_shopping_rows(raw["workshop"])
        if "cards" in raw:
            cards = raw["cards"]
            if not isinstance(cards, Mapping):
                raise ControlError("cards", "cards must be a mapping")
            card_fields = {f.name for f in dataclasses.fields(CardPolicy)}
            for key in cards:
                if key not in card_fields:
                    raise ControlError(key, f"unknown cards field {key!r}")
            values["cards"] = CardPolicy(**cards)
        try:
            return cls(**values)
        except ControlError:
            raise
        except (TypeError, ValueError) as exc:
            raise ControlError("shopping", str(exc)) from None


def _parse_shopping_rows(raw: Any) -> tuple[ShoppingRule, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ControlError(
            "workshop", f"workshop must be a list, not {type(raw).__name__!r}"
        )
    rule_fields = {f.name for f in dataclasses.fields(ShoppingRule)}
    rules: list[ShoppingRule] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ControlError("workshop", "each shopping row must be a mapping")
        for key in entry:
            if key not in rule_fields:
                raise ControlError(key, f"unknown shopping row field {key!r}")
        try:
            rules.append(ShoppingRule(**entry))
        except TypeError as exc:
            raise ControlError("workshop", str(exc)) from None
    return tuple(rules)
```

Then wire it into `Strategy`:

1. Add the field, after `screen_confirmations`:
   ```python
       # Between-runs spending. Defaults to a policy that buys nothing, so a
       # strategy file written before this existed loads and behaves the same.
       shopping: Shopping = Shopping()
   ```
2. Add `"shopping": (Shopping,)` to `_STRATEGY_TYPES`.
3. In `to_dict()`, add `"shopping": self.shopping.to_dict()`.
4. In `from_dict()`, before building `values`, pull `shopping` out and parse it:
   ```python
       shopping = (
           Shopping.from_dict(raw["shopping"]) if "shopping" in raw else Shopping()
       )
       values = {k: raw[k] for k in raw if k not in ("actions", "shopping")}
       ...
       return cls(actions=rules, shopping=shopping, **values)
   ```
5. In `merged()`, after the `actions` branch:
   ```python
       if "shopping" in patch:
           updates["shopping"] = Shopping.from_dict(patch["shopping"])
   ```
6. Add `"shopping"` to `PATCHABLE_FIELDS`? **No** — it is handled explicitly in
   `merged()` exactly as `actions` is, because it needs parsing rather than
   copying. Leave `PATCHABLE_FIELDS` alone and add a comment saying so beside
   the existing note about `actions`.
7. In `validated()`, after the actions loop, run the identical containment
   check over `self.shopping.workshop`. Extract the per-rule body into a local
   helper `_check_template(name, template)` and call it from both loops rather
   than copying twenty lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_shopping.py tests/test_strategy.py -q`
Expected: PASS. `tests/test_strategy.py` must stay green.

- [ ] **Step 5: Commit**

```bash
git add strategy.py tests/test_strategy_shopping.py
git commit -m "feat: add the shopping policy to Strategy

enabled and armed are two switches, not one mode: enabled+unarmed is the
rehearsal, and armed is the only field in this config standing between a
miscalibrated template and a currency with no refund.

Tab visit order is derived from row order rather than fixed, so reordering
rows is the only control anyone needs, and a tab whose rows are all disabled
is never opened."
```

---

### Task 7: Shopping events

**Files:**
- Modify: `events.py`
- Test: `tests/test_shopping_events.py`

**Interfaces:**
- Produces: `events.PageChanged(prev: str, curr: str, confidence: float)`; `events.ShoppingStarted(visit: int, coins: int | None, gems: int | None, dry_run: bool)`; `events.Purchased(item: str, category: str, price: int | None, coins_before: int | None, dry_run: bool)`; `events.PurchaseSkipped(item: str, reason: str, detail: str = "")`; `events.ShoppingEnded(visit: int, bought: int, spent: int, aborted: bool = False, reason: str = "")`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shopping_events.py
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
        events.PageChanged(prev="MAIN_MENU", curr="WORKSHOP", confidence=0.99),
        events.ShoppingStarted(visit=1, coins=1770, gems=40, dry_run=True),
        events.Purchased(
            item="Damage", category="ATTACK", price=30, coins_before=1770, dry_run=True
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shopping_events.py -q`
Expected: FAIL with `AttributeError: module 'events' has no attribute 'Purchased'`

- [ ] **Step 3: Write minimal implementation**

Append to `events.py`, after `Navigated`:

```python
@dataclass(frozen=True, kw_only=True)
class PageChanged(Event):
    """Which MENU page is showing. Separate from ScreenChanged, which tracks
    the run lifecycle - see pages.py for why the two never merge."""

    prev: str
    curr: str
    confidence: float


@dataclass(frozen=True, kw_only=True)
class ShoppingStarted(Event):
    visit: int
    coins: int | None = None
    gems: int | None = None
    dry_run: bool = True


@dataclass(frozen=True, kw_only=True)
class Purchased(Event):
    """One thing bought, or - when dry_run - one thing that would have been.

    `price` and `coins_before` are None rather than 0 when they could not be
    read. Zero is a free upgrade; None is "we did not know", and collapsing
    the two would make an unreadable price look like a bargain in the log.
    """

    item: str
    category: str
    price: int | None = None
    coins_before: int | None = None
    dry_run: bool = True


@dataclass(frozen=True, kw_only=True)
class PurchaseSkipped(Event):
    item: str
    reason: str  # unaffordable | unreadable | no_match | disabled | capped
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class ShoppingEnded(Event):
    visit: int
    bought: int
    spent: int
    aborted: bool = False
    reason: str = ""
```

Check `sinks/store.py:to_row` — if it maps typed columns by attribute name, `Purchased.price` lands in the `price` column with no change. If it whitelists event types, add the new ones to that whitelist and nothing else.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_shopping_events.py tests/test_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add events.py tests/test_shopping_events.py
git commit -m "feat: add the shopping events

price and coins_before are None rather than 0 when unreadable: zero is a free
upgrade, None is 'we did not know', and collapsing them makes a failed read
look like a bargain in the log.

A test pins that none of these needs a column the events table lacks - the
JSON detail blob is exactly what it was built for."
```

---

### Task 8: `ShoppingSession`, dry-run only

The core. It taps nothing yet: `armed` gates the tap, and every test here asserts the fake device received no input.

**Files:**
- Create: `shopping.py`
- Test: `tests/test_shopping.py`

**Interfaces:**
- Consumes: `pages.classify_page`, `strategy.Shopping`, `config.WORKSHOP_TABS/WORKSHOP_ROWS/CARD_BUTTONS/ROW_PRICE_REGION/HEADER_REGIONS`, `digits.NumberReader`, `vision.locate_template`, `device.tap`, `events.EventBus`.
- Produces:
  - `shopping.Step` — `Enum` with `IDLE`, `OPEN_WORKSHOP`, `OPEN_TAB`, `BUY_ROWS`, `OPEN_CARDS`, `BUY_CARDS`, `RETURN`
  - `shopping.ShoppingSession(templates, bus, reader, shopping=..., threshold=0.8)`
    - `.active: bool` — True whenever `step is not Step.IDLE`
    - `.begin(shopping: Shopping, run_count: int) -> bool` — start a visit if the policy says so; False otherwise
    - `.advance(screen: Image, device, shopping: Shopping) -> None` — one step
  - `shopping.header_numbers(screen, page, top_left, reader) -> tuple[int | None, int | None]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shopping.py
"""The shopping state machine, with no device and no emulator.

Frames come from the committed fixtures, so this exercises the real matcher
and the real digit reader against real pixels - only the tapping is faked.

Every dry-run test asserts the fake device recorded ZERO taps. That is the
property the whole rehearsal rests on, so it is asserted directly rather than
inferred from the events.
"""

from pathlib import Path

import cv2
import pytest

import config
import digits
import events
import shopping as shopping_mod
import vision
from strategy import CardPolicy, Shopping, ShoppingRule

FIXTURES = Path(__file__).parent / "fixtures"


class FakeDevice:
    """Records taps instead of sending them."""

    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []


@pytest.fixture(autouse=True)
def capture_taps(monkeypatch):
    """device.tap is module-level in shopping.py, so patch it there."""
    def _tap(device, x, y):
        device.taps.append((x, y))

    monkeypatch.setattr(shopping_mod, "tap", _tap)


class Recorder:
    """A bus that keeps what it was given."""

    def __init__(self) -> None:
        self.published: list[events.Event] = []

    def publish(self, event):
        self.published.append(event)
        return event

    def of_type(self, name: str):
        return [e for e in self.published if e.type == name]


def frame(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    return img


def a_policy(**over) -> Shopping:
    base = dict(
        enabled=True,
        armed=False,
        workshop=(
            ShoppingRule(name="Unlock Cash Bonuses",
                         template="workshop/unlock_cash_bonuses.png",
                         category="UTILITY"),
            ShoppingRule(name="Health", template="workshop/row_health.png",
                         category="DEFENSE"),
            ShoppingRule(name="Damage", template="workshop/row_damage.png",
                         category="ATTACK"),
        ),
    )
    return Shopping(**{**base, **over})


@pytest.fixture
def session():
    return shopping_mod.ShoppingSession(
        templates=vision.TemplateCache(config.TEMPLATE_DIR),
        bus=Recorder(),
        reader=digits.NumberReader(),
    )


# -- starting a visit ------------------------------------------------------
def test_a_disabled_policy_never_starts_a_visit(session) -> None:
    assert session.begin(a_policy(enabled=False), run_count=1) is False
    assert session.active is False


def test_an_enabled_policy_starts_a_visit(session) -> None:
    assert session.begin(a_policy(), run_count=1) is True
    assert session.active is True


def test_the_cadence_skips_runs_between_visits(session) -> None:
    policy = a_policy(visit_every_n_runs=3)
    assert session.begin(policy, run_count=1) is True
    session.reset()
    assert session.begin(policy, run_count=2) is False
    assert session.begin(policy, run_count=3) is False
    assert session.begin(policy, run_count=4) is True


def test_a_policy_with_no_enabled_rows_and_no_cards_starts_nothing(session) -> None:
    """A visit that would buy nothing is a minute of tab-tapping for free."""
    policy = a_policy(workshop=(), cards=CardPolicy(enabled=False))
    assert session.begin(policy, run_count=1) is False


# -- reading the header ----------------------------------------------------
def test_the_header_reads_coins_and_gems_off_a_workshop_frame() -> None:
    reader = digits.NumberReader()
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    screen = frame("menu_workshop_attack")
    _, top_left = vision.best_score(screen, cache.get(config.PAGE_ANCHORS["WORKSHOP"]))
    coins, gems = shopping_mod.header_numbers(screen, "WORKSHOP", top_left, reader)
    assert coins == 1770
    assert gems == 40


# -- the dry run taps nothing ---------------------------------------------
def test_a_whole_unarmed_visit_taps_nothing(session) -> None:
    """The property the rehearsal exists for, asserted directly."""
    device = FakeDevice()
    policy = a_policy(armed=False)
    session.begin(policy, run_count=1)
    for name in ("menu_main", "menu_workshop_utility", "menu_workshop_defense",
                 "menu_workshop_attack", "menu_cards", "menu_main"):
        for _ in range(4):
            session.advance(frame(name), device, policy)
    assert device.taps == []


def test_an_unarmed_visit_still_reports_what_it_would_buy(session) -> None:
    device = FakeDevice()
    policy = a_policy(armed=False)
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_utility"), device, policy)
    session.advance(frame("menu_workshop_utility"), device, policy)
    purchases = session._bus.of_type("Purchased")
    assert purchases, "a rehearsal that reports nothing proves nothing"
    assert all(p.dry_run for p in purchases)
    assert purchases[0].item == "Unlock Cash Bonuses"
    assert purchases[0].price == 40


# -- the buying rule -------------------------------------------------------
def test_the_highest_priority_affordable_row_wins_not_the_cheapest(session) -> None:
    """Order is the whole policy. Critical Chance costs 50 and Damage 30;
    with Critical Chance listed first it must be the one chosen."""
    device = FakeDevice()
    policy = a_policy(workshop=(
        ShoppingRule(name="Critical Chance",
                     template="workshop/row_critical_chance.png", category="ATTACK"),
        ShoppingRule(name="Damage", template="workshop/row_damage.png",
                     category="ATTACK"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)
    bought = session._bus.of_type("Purchased")
    assert bought[0].item == "Critical Chance"


def test_a_row_costing_more_than_the_balance_is_skipped_as_unaffordable(session) -> None:
    device = FakeDevice()
    policy = a_policy()
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    # menu_workshop.png is the older capture: 78 coins, so the 50-coin unlock
    # is affordable but a 100-coin row would not be.
    session._coins = 10
    session.advance(frame("menu_workshop_attack"), device, policy)
    skips = session._bus.of_type("PurchaseSkipped")
    assert any(s.reason == "unaffordable" for s in skips)


def test_an_unreadable_balance_stops_the_visit_rather_than_guessing(session) -> None:
    """There is no brightness fallback on a menu page, and guessing is the
    failure mode that costs coins."""
    device = FakeDevice()
    policy = a_policy()
    session.begin(policy, run_count=1)
    session._coins = None
    session.advance(frame("menu_workshop_attack"), device, policy)
    ended = session._bus.of_type("ShoppingEnded")
    skipped = session._bus.of_type("PurchaseSkipped")
    assert ended or any(s.reason == "unreadable" for s in skipped)
    assert device.taps == []


# -- category order --------------------------------------------------------
def test_tabs_are_visited_in_the_order_the_rows_imply(session) -> None:
    policy = a_policy()
    session.begin(policy, run_count=1)
    assert session.remaining_categories() == ["UTILITY", "DEFENSE", "ATTACK"]


# -- bailing out -----------------------------------------------------------
def test_the_tap_budget_ends_the_visit(session) -> None:
    device = FakeDevice()
    policy = a_policy(armed=True, max_taps_per_visit=2)
    session.begin(policy, run_count=1)
    for _ in range(20):
        session.advance(frame("menu_workshop_attack"), device, policy)
    assert len(device.taps) <= 2
    ended = session._bus.of_type("ShoppingEnded")
    assert ended and ended[-1].aborted


def test_an_unexpected_page_twice_running_ends_the_visit(session) -> None:
    """Once is an animation. Twice is lost."""
    device = FakeDevice()
    policy = a_policy()
    session.begin(policy, run_count=1)
    session.advance(frame("in_run_lit"), device, policy)
    assert session.active is True, "one odd frame is not enough to give up"
    session.advance(frame("in_run_lit"), device, policy)
    ended = session._bus.of_type("ShoppingEnded")
    assert ended and ended[-1].aborted
    assert session.active is False


def test_a_finished_visit_returns_to_idle(session) -> None:
    device = FakeDevice()
    policy = a_policy(cards=CardPolicy(enabled=False))
    session.begin(policy, run_count=1)
    for name in ("menu_main", "menu_workshop_utility", "menu_workshop_defense",
                 "menu_workshop_attack", "menu_main"):
        for _ in range(6):
            session.advance(frame(name), device, policy)
    assert session.active is False


# -- cards -----------------------------------------------------------------
def test_cards_are_not_bought_when_the_policy_is_off(session) -> None:
    device = FakeDevice()
    policy = a_policy(armed=True, cards=CardPolicy(enabled=False))
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.BUY_CARDS
    session.advance(frame("menu_cards"), device, policy)
    assert device.taps == []


def test_the_gem_floor_stops_card_buying(session) -> None:
    """40 gems, a 20-gem card and a floor of 40: buying would breach it."""
    device = FakeDevice()
    policy = a_policy(armed=True, cards=CardPolicy(enabled=True, gem_floor=40))
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.BUY_CARDS
    session._gems = 40
    session.advance(frame("menu_cards"), device, policy)
    assert device.taps == []
    skips = session._bus.of_type("PurchaseSkipped")
    assert any(s.reason == "capped" for s in skips)


def test_card_buying_stops_at_the_per_visit_cap(session) -> None:
    device = FakeDevice()
    policy = a_policy(armed=True, cards=CardPolicy(
        enabled=True, gem_floor=0, max_per_visit=1
    ))
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.BUY_CARDS
    session._gems = 400
    for _ in range(5):
        session.advance(frame("menu_cards"), device, policy)
    bought = [e for e in session._bus.of_type("Purchased") if e.category == "CARDS"]
    assert len(bought) == 1


def test_the_bot_never_taps_unlock_new_slot(session) -> None:
    """Slots are out of scope by design - the community gem order puts lab
    slots above them and the bot cannot see labs. Guarded by the fact that no
    slot template exists at all, which this pins."""
    assert not any("slot" in path.lower() for path in config.CARD_BUTTONS.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shopping.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shopping'`

- [ ] **Step 3: Write the implementation**

Create `shopping.py`. Structure it as:

- Module docstring stating plainly that unlike `navigate.py`, this module **does** spend permanent resources, and that `armed` is what stands between it and your coins.
- `Step` enum.
- `header_numbers(screen, page, top_left, reader)` — returns `(coins, gems)`, both `None` when the page has no `HEADER_REGIONS` entry.
- `ShoppingSession` holding: `_step`, `_visit`, `_taps`, `_bought`, `_spent`, `_cards_bought`, `_coins`, `_gems`, `_categories` (a list, popped as visited), `_last_run_count`, `_off_page_streak`.

Key methods:

```python
    def begin(self, shopping: Shopping, run_count: int) -> bool:
        """Start a visit, or decline with a reason.

        Declines when shopping is off, when nothing is enabled to buy, and
        when the cadence says this run is not a visiting run. Returns a bool
        rather than raising because "not this run" is the normal case, not an
        error.
        """
```

```python
    def advance(self, screen: Image, device: Any, shopping: Shopping) -> None:
        """One step. At most one tap, and only when shopping.armed.

        Every path through here must either make progress, publish a skip, or
        end the visit. A step that silently does nothing is how a session
        wedges, which is what _off_page_streak exists to catch.
        """
```

Rules the implementation must hold to, each already asserted by a test above:

- `_tap(device, x, y, shopping)` is the ONLY place `tap` is called, and it
  returns early when `not shopping.armed`. Every purchase publishes
  `Purchased(dry_run=not shopping.armed)` either way.
- The tap budget is checked before tapping, not after.
- `_coins is None` ends the visit; there is no brightness fallback here.
- `BUY_ROWS` re-reads coins from the header on every step, rather than
  subtracting the price it just paid: the game is the source of truth, and a
  subtraction that drifts spends money the bot does not have.
- A row that does not match on the page it is configured for publishes
  `PurchaseSkipped(reason="no_match")` once and is not retried this visit —
  otherwise a mis-cut template loops until the tap budget runs out.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_shopping.py -q`
Expected: PASS, 18 tests.

- [ ] **Step 5: Commit**

```bash
git add shopping.py tests/test_shopping.py
git commit -m "feat: add the shopping session, dry-run only

One step per scan, so a visit keeps pause, the frame stream and the event
feed working the way every other thing the loop does already.

Unlike navigate.py, this module spends permanent resources - the docstring
says so - and armed is the single gate. Every dry-run test asserts the fake
device recorded zero taps rather than inferring it from the events."
```

---

### Task 9: Wire the session into the scan loop

**Files:**
- Modify: `tower_bot.py:70-127` (constructor), `tower_bot.py:332-465` (`run_once`)
- Modify: `runner.py` (pass the session through)
- Test: `tests/test_shopping_loop.py`

**Interfaces:**
- Consumes: `shopping.ShoppingSession` (Task 8).
- Produces: `TowerBot.shopping: ShoppingSession`; `TowerBot.runs_completed: int` used as the visit cadence counter.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shopping_loop.py
"""How a shopping visit sits inside the scan loop.

Three collaborators read the same frame - the screen tracker, the run tracker
and the shopping session - and the interactions between them are where this
feature can break something that already works. Each one gets a test.
"""

import pytest

import events
import screens
import shopping as shopping_mod
from strategy import Shopping, ShoppingRule, Strategy


def a_policy(**over) -> Shopping:
    base = dict(
        enabled=True, armed=False,
        workshop=(ShoppingRule(name="Damage", template="workshop/row_damage.png",
                               category="ATTACK"),),
    )
    return Shopping(**{**base, **over})


def test_navigation_is_suppressed_while_a_visit_is_live(bot_on_main_menu) -> None:
    """Navigator taps BATTLE on MAIN_MENU. Left alone it starts a run in the
    middle of an errand."""
    bot = bot_on_main_menu(a_policy())
    bot.shopping.begin(a_policy(), run_count=1)
    assert bot.shopping.active
    bot.run_once()
    assert bot.navigator.last_target is None, "BATTLE was tapped mid-visit"


def test_navigation_resumes_once_the_visit_ends(bot_on_main_menu) -> None:
    bot = bot_on_main_menu(a_policy(enabled=False))
    assert not bot.shopping.active
    bot.run_once()
    assert bot.navigator.last_target == "BATTLE"


def test_unknown_snapshots_are_suppressed_while_a_visit_is_live(bot_on_workshop) -> None:
    """A workshop page reads UNKNOWN to the screen tracker by design. Without
    this, every visit fills unknown/ with pictures of the workshop and evicts
    the genuine mysteries the directory exists to hold."""
    bot = bot_on_workshop(a_policy())
    bot.shopping.begin(a_policy(), run_count=1)
    for _ in range(4):
        bot.run_once()
    assert bot.snapshots.written == []


def test_unknown_snapshots_still_happen_outside_a_visit(bot_on_workshop) -> None:
    """The suppression must be scoped to a live visit, not switched off."""
    bot = bot_on_workshop(a_policy(enabled=False))
    for _ in range(4):
        bot.run_once()
    assert bot.snapshots.written, "an unmodelled screen must still leave a trail"


def test_a_visit_does_not_open_or_close_a_run(bot_on_main_menu) -> None:
    bot = bot_on_main_menu(a_policy())
    bot.shopping.begin(a_policy(), run_count=1)
    before = bot.runs.current_id
    for _ in range(4):
        bot.run_once()
    assert bot.runs.current_id == before


def test_no_visit_starts_while_a_run_is_live(bot_in_run) -> None:
    """A visit begins from MAIN_MENU only. Starting one mid-fight would tap
    the workshop tab over a live run."""
    bot = bot_in_run(a_policy())
    for _ in range(3):
        bot.run_once()
    assert not bot.shopping.active


def test_a_paused_bot_does_not_shop(bot_on_main_menu) -> None:
    """Pause means 'still scanning, not tapping', and that must cover the one
    tap path that costs money."""
    bot = bot_on_main_menu(a_policy(armed=True))
    bot.controls.apply({"paused": True})
    for _ in range(4):
        bot.run_once()
    assert bot.device.taps == []


def test_reaching_the_run_cap_does_not_start_a_visit(bot_on_main_menu) -> None:
    bot = bot_on_main_menu(a_policy())
    for _ in range(3):
        bot.run_once(max_runs=1)
    assert not bot.shopping.active


def test_shopping_is_disabled_when_the_header_atlas_is_missing(monkeypatch) -> None:
    """No coin read means no purchase can be approved, so say so once at
    startup rather than failing silently on every visit."""
    import digits
    import tower_bot

    monkeypatch.setattr(digits.AtlasCache, "get", lambda self, name: None)
    session = tower_bot.build_shopping(bus=None, templates=None)
    assert session is None or session.disabled_reason
```

Add the three fixtures (`bot_on_main_menu`, `bot_on_workshop`, `bot_in_run`) to `tests/conftest.py` if they do not already exist, building a `TowerBot` with a fake device that returns a fixed fixture frame and records taps, and a `SnapshotWriter` double that records rather than writes.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shopping_loop.py -q`
Expected: FAIL — fixtures missing, then `AttributeError: 'TowerBot' object has no attribute 'shopping'`.

- [ ] **Step 3: Write minimal implementation**

In `tower_bot.py`, add a `shopping` keyword to `TowerBot.__init__` defaulting to `None` and build a disabled session when absent, so every existing test that constructs a bot keeps working.

In `run_once`, make exactly these changes:

1. After the tracker/run bookkeeping, before the unknown-snapshot block:
   ```python
        # A visit owns the frame while it runs. Three things below key off
        # this rather than off the screen state, because the pages a visit
        # walks are UNKNOWN to the tracker by design - see pages.py.
        visiting = self.shopping.active
   ```
2. Guard the snapshot block with `and not visiting`.
3. Guard the navigation block with `and not visiting`.
4. After the navigation block, drive the session:
   ```python
        if visiting:
            self.shopping.advance(self.screen, self.device, settings.strategy.shopping)
        elif (
            state is screens.ScreenState.MAIN_MENU
            and not settings.paused
            and not self.run_cap_reached(max_runs, settings.strategy)
        ):
            self.shopping.begin(settings.strategy.shopping, self.runs.completed)
   ```
   Order matters: `begin` is checked *after* `advance` so a visit that just
   ended does not immediately restart in the same scan.

Add `build_shopping(bus, templates, reader=None) -> ShoppingSession` beside `build_checks`, which checks the header atlas for the labels Task 3 requires and returns a session carrying `disabled_reason` when they are missing. Log the reason once, at startup, the way `build_affordability` already logs its downgrade.

Thread the session through `BotRunner` the same way `checks` is — built once per process, not per bot.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_shopping_loop.py tests/test_tower_bot.py tests/test_runner.py -q`
Expected: PASS. The existing bot and runner tests must stay green.

- [ ] **Step 5: Commit**

```bash
git add tower_bot.py runner.py tests/test_shopping_loop.py tests/conftest.py
git commit -m "feat: drive the shopping session from the scan loop

Three suppressions, each with a test: navigation cannot tap BATTLE mid-visit,
unknown-screen snapshots do not fill unknown/ with pictures of the workshop,
and a visit neither opens nor closes a run.

begin() is checked after advance() so a visit that just ended cannot restart
in the same scan. Shopping disables itself with a logged reason when the
header atlas is incomplete - an unreadable balance can approve no purchase."
```

---

### Task 10: The default strategy's shopping rows

**Files:**
- Modify: `strategies/default.json`, `strategy.py` (`Strategy.from_config`)
- Test: `tests/test_strategy_shopping.py`

**Interfaces:**
- Consumes: Task 6's schema, Task 5's templates.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_strategy_shopping.py
def test_the_shipped_default_ships_disarmed() -> None:
    """Nobody should be able to clone this repo and have it spend coins."""
    default = Strategy.from_config()
    assert default.shopping.armed is False


def test_the_shipped_default_leads_with_the_unlock_tiles() -> None:
    """On a fresh account every upgrade the community names first is behind
    one of these, and all three together cost 165 coins."""
    rows = [r.name for r in Strategy.from_config().shopping.workshop if r.enabled]
    assert rows[:3] == [
        "Unlock Cash Bonuses", "Unlock Defense Upgrades", "Unlock Range Upgrades"
    ]


def test_the_crit_rows_ship_switched_off() -> None:
    """Present so there is a row to switch on; off because the community is
    unanimous that crit costs more and scales slower early."""
    by_name = {r.name: r for r in Strategy.from_config().shopping.workshop}
    assert by_name["Critical Chance"].enabled is False
    assert by_name["Critical Factor"].enabled is False


def test_the_shipped_default_never_spends_gems() -> None:
    assert Strategy.from_config().shopping.cards.enabled is False


def test_every_shipped_row_has_a_template_on_disk() -> None:
    Strategy.from_config().validated()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_shopping.py -q -k shipped`
Expected: FAIL — `Strategy.from_config()` returns an empty `Shopping`.

- [ ] **Step 3: Write minimal implementation**

Add to `config.py` a `SHOPPING_ROWS` tuple in the spec's §7 order, and have `Strategy.from_config()` build `Shopping(workshop=...)` from it — keeping `config.py` the origin of the shipped defaults exactly as it already is for `ACTIONS`.

```python
# Shipped buy order. Taken from the community consensus (see the dashboard's
# Guide page) and cut down to rows this account can actually SEE: everything
# the guides name first - Cash/Wave, Coins/Wave, Def Abs, Def%, Thorns,
# Coins/Kill - is behind one of the three unlock tiles, which is why they
# lead. Together they cost 165 coins.
#
# The crit rows ship disabled. They are here so there is a row to switch on,
# which is the same reason `enabled` exists at all.
SHOPPING_ROWS: tuple[tuple[str, str, bool], ...] = (
    ("Unlock Cash Bonuses", "UTILITY", True),
    ("Unlock Defense Upgrades", "DEFENSE", True),
    ("Unlock Range Upgrades", "ATTACK", True),
    ("Health", "DEFENSE", True),
    ("Health Regen", "DEFENSE", True),
    ("Damage", "ATTACK", True),
    ("Attack Speed", "ATTACK", True),
    ("Critical Chance", "ATTACK", False),
    ("Critical Factor", "ATTACK", False),
)
```

Regenerate `strategies/default.json` from the new default and commit it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_shopping.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config.py strategy.py strategies/default.json tests/test_strategy_shopping.py
git commit -m "feat: ship a community-ordered default buy list

Leads with the three unlock tiles because on a fresh account every upgrade
the guides name first is behind one of them, and all three cost 165 coins
together. Crit ships switched off; cards ship off; armed ships false.

Rows the account cannot see yet are absent rather than stubbed: a template
never cut against a real frame matches nothing or matches everything."
```

---

### Task 11: The Guide page

**Files:**
- Create: `web/ui/app/guide/page.tsx`, `web/ui/app/guide/page.test.tsx`
- Modify: `web/ui/components/Sidebar.tsx`

**Interfaces:**
- Produces: a static route at `/guide/`. No new API surface.

- [ ] **Step 1: Write the failing test**

```tsx
// web/ui/app/guide/page.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GuidePage from "./page";

describe("Guide", () => {
  it("gives the workshop order the bot actually ships with", () => {
    render(<GuidePage />);
    expect(screen.getByText(/Unlock Cash Bonuses/)).toBeInTheDocument();
    expect(screen.getByText(/Coins\/Kill/)).toBeInTheDocument();
  });

  it("says where this account is, not just where the guides assume", () => {
    render(<GuidePage />);
    expect(screen.getByRole("heading", { name: /where this account is/i })).toBeInTheDocument();
  });

  it("states what the bot will not do and why", () => {
    render(<GuidePage />);
    const limits = screen.getByRole("region", { name: /what the bot will not do/i });
    expect(limits).toHaveTextContent(/lab slots/i);
    expect(limits).toHaveTextContent(/ultimate weapon/i);
  });

  it("cites a source for every section", () => {
    render(<GuidePage />);
    const links = screen.getAllByRole("link", { name: /tower-hub/i });
    expect(links.length).toBeGreaterThanOrEqual(4);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web/ui && npm test -- guide`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the page**

Static content, using the existing `Card` primitives. Sections, with the content from the spec's §10:

1. **Where this account is** — 1.77K coins, 40 gems, Tier 1. The three unlock tiles and their prices. Says plainly that the general advice below describes upgrades not yet visible.
2. **Workshop order** — economy (Cash/Wave, Coins/Wave → Cash Bonus, Coins/Kill after wave 100), defense (Def Abs ~50 → Def% → Health 1–2 → Thorns 1% above 6/11/21/26%), attack last (Damage, Attack Speed; crit deferred).
3. **Gems** — lab slots first; then Attack Speed, Enemy Balance, Coins cards + slots; third lab slot; Health and Cash; fourth lab slot; then Crit Coin / Wave Skip / Extra Orbs. Do not chase epics; relics are worse value than modules and card levels.
4. **Card mechanics** — 20 gems each, 200 for ten; 80% common / 17% rare / 3% epic, rare rising to 97% once commons max; 80 copies (1,600 gems) to max a card; the two-week 80-card event.
5. **What the bot will not do, and why** — `role="region"` with `aria-label="What the bot will not do"`. Lab slots outrank cards and the bot cannot see labs. UW picks are irreversible and the single most-cited account-damaging mistake. Card equipping depends on build and tier. Each limitation with its reason.

Every section ends with its source link:
`https://www.tower-hub.com/wiki/guide/beginner-guide`, `/gem-guide`, `/coin-guide-basics`, `/footguns`, `https://www.tower-hub.com/wiki/card/cards`.

Add `{ href: "/guide/", label: "Guide" }` to `LINKS` in `Sidebar.tsx`, between Strategy and Control.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web/ui && npm test -- guide`
Expected: PASS, 4 tests.

- [ ] **Step 5: Build the static export and commit**

```bash
cd web/ui && npm run build && cd ../..
git add web/ui/app/guide web/ui/components/Sidebar.tsx web/static
git commit -m "feat: add the Guide page

The community strategy the buy order is drawn from, sourced, with a section
saying where this account actually is - the general advice describes upgrades
that are still behind unlock tiles here, and a guide that does not say so is
misleading.

Also states what the bot will not do and why: lab slots outrank cards and it
cannot see labs, UW picks are irreversible, card equipping depends on build."
```

---

### Task 12: The Strategy page's Shopping section

**Files:**
- Create: `web/ui/components/ShoppingEditor.tsx`, `web/ui/components/ShoppingEditor.test.tsx`
- Modify: `web/ui/app/strategy/page.tsx`, `web/ui/lib/types.ts`

**Interfaces:**
- Consumes: `PATCH /api/control` with a `shopping` key (Task 6).
- Produces: `ShoppingEditor({ shopping, onChange, disabled })`; `types.ts` gains `ShoppingRule`, `CardPolicy`, `Shopping`, and `Strategy.shopping`.

- [ ] **Step 1: Write the failing test**

```tsx
// web/ui/components/ShoppingEditor.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShoppingEditor } from "./ShoppingEditor";

const policy = {
  enabled: true,
  armed: false,
  visit_every_n_runs: 1,
  max_taps_per_visit: 40,
  workshop: [
    { name: "Unlock Cash Bonuses", template: "workshop/unlock_cash_bonuses.png",
      category: "UTILITY", enabled: true, threshold: 0.9, brightness_ratio: 0.75 },
    { name: "Damage", template: "workshop/row_damage.png",
      category: "ATTACK", enabled: true, threshold: 0.9, brightness_ratio: 0.75 },
  ],
  cards: { enabled: false, gem_floor: 40, max_per_visit: 2, batch: "x1" },
};

describe("ShoppingEditor", () => {
  it("shows rows in priority order", () => {
    render(<ShoppingEditor shopping={policy} onChange={vi.fn()} />);
    const rows = screen.getAllByTestId("shopping-row");
    expect(rows[0]).toHaveTextContent("Unlock Cash Bonuses");
  });

  it("says plainly that an unarmed policy spends nothing", () => {
    render(<ShoppingEditor shopping={policy} onChange={vi.fn()} />);
    expect(screen.getByText(/rehearsal/i)).toBeInTheDocument();
  });

  it("requires a confirmation before arming", () => {
    const onChange = vi.fn();
    render(<ShoppingEditor shopping={policy} onChange={onChange} />);
    fireEvent.click(screen.getByRole("switch", { name: /arm/i }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /yes, spend coins/i }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ armed: true }),
    );
  });

  it("disarms without asking", () => {
    const onChange = vi.fn();
    render(<ShoppingEditor shopping={{ ...policy, armed: true }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("switch", { name: /arm/i }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ armed: false }));
  });

  it("carries a hint explaining why each row sits where it does", () => {
    render(<ShoppingEditor shopping={policy} onChange={vi.fn()} />);
    expect(screen.getByTestId("hint-Unlock Cash Bonuses")).toHaveTextContent(/unlock/i);
  });

  it("warns that the gem floor guards a currency with no refund", () => {
    render(<ShoppingEditor shopping={policy} onChange={vi.fn()} />);
    expect(screen.getByLabelText(/gem floor/i)).toBeInTheDocument();
    expect(screen.getByText(/cannot be earned back quickly/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web/ui && npm test -- ShoppingEditor`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component and types**

Add to `web/ui/lib/types.ts`:

```ts
export interface ShoppingRule {
  name: string;
  template: string;
  category: "ATTACK" | "DEFENSE" | "UTILITY";
  enabled: boolean;
  threshold: number;
  brightness_ratio: number;
}

export interface CardPolicy {
  enabled: boolean;
  gem_floor: number;
  max_per_visit: number;
  batch: "x1" | "x10";
}

/** Mirrors strategy.py's Shopping.to_dict(). `enabled` and `armed` are two
 * switches: enabled+unarmed reads and reports without tapping. */
export interface Shopping {
  enabled: boolean;
  armed: boolean;
  visit_every_n_runs: number;
  max_taps_per_visit: number;
  workshop: ShoppingRule[];
  cards: CardPolicy;
}
```

and `shopping: Shopping;` to the `Strategy` interface.

Build `ShoppingEditor` following `StrategyEditor`'s existing patterns for row reordering and per-row numeric fields. The arm switch is visually distinct and gated behind a confirm dialog whose button reads "Yes, spend coins"; disarming is immediate. Each row carries a `data-testid={`hint-${name}`}` one-liner drawn from the Guide content.

Mount it in `web/ui/app/strategy/page.tsx` below the existing action editor.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web/ui && npm test -- ShoppingEditor strategy`
Expected: PASS.

- [ ] **Step 5: Build and commit**

```bash
cd web/ui && npm run build && cd ../..
git add web/ui/components/ShoppingEditor.tsx web/ui/components/ShoppingEditor.test.tsx \
        web/ui/app/strategy web/ui/lib/types.ts web/static
git commit -m "feat: edit the shopping policy from the Strategy page

Arming asks for confirmation; disarming does not. It is the only control in
this dashboard that spends something you cannot get back, so it does not look
like the checkboxes beside it.

Each row carries a one-line hint from the Guide, so the reasoning sits next
to the knob rather than a click away."
```

---

### Task 13: Calibrate the buy point and arm

The only task that spends a coin. Do not start it without the user watching.

**Files:**
- Modify: `config.py` (`workshop_buy_point`), `shopping.py`
- Test: `tests/test_shopping_templates.py`

**Interfaces:**
- Produces: `config.workshop_buy_point(anchor: tuple[int, int]) -> tuple[int, int]`

**Why this is last:** in-run, a row's label is itself a button that opens an info panel — `config.buy_point()` exists entirely because of that trap. Whether the workshop tile behaves the same way is unknown, and a wrong guess opens a panel that blinds the next scan. It is settled by one deliberate tap, watched, on the cheapest row.

- [ ] **Step 1: Add the buy point, derived not measured twice**

```python
def workshop_buy_point(anchor: tuple[int, int]) -> tuple[int, int]:
    """Where to tap to BUY the workshop row matched at `anchor`.

    Derived from ROW_PRICE_REGION rather than measured separately, the same
    way buy_point() is derived from PRICE_REGION: that offset already locates
    the price strip inside this row's tile, from this same anchor, for every
    row. One calibration to keep correct instead of two that drift apart.

    VERIFIED on the live device - see the calibration step in the menu
    shopping plan. Until that ran, this was a guess carried over from the
    in-run geometry.
    """
    return (
        anchor[0] + ROW_PRICE_REGION.dx + ROW_PRICE_REGION.w // 2,
        anchor[1] + ROW_PRICE_REGION.dy + ROW_PRICE_REGION.h // 2,
    )
```

- [ ] **Step 2: Rehearse against the live device**

Start the dashboard with shopping enabled and **unarmed**, let a visit run, and read the feed:

```bash
export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools"
uv run tower_bot.py --web --idle
```

Confirm in the browser: the visit walks Utility → Defense → Attack → Cards → Battle; the coin and gem balances match the screen; every `Purchased` is tagged dry-run; the rows named are the ones you would buy. **Do not proceed while any of those is wrong.**

- [ ] **Step 3: One watched tap**

With the user present, arm the policy, let it buy exactly one row, and disarm. Then confirm on the device that the coin balance dropped by the price and no info panel is open.

If a panel opened instead, the tile's buy target is not where the price is. Capture that frame, measure the real target with `tools/crop_preview.py`, and give `workshop_buy_point` its own region rather than deriving it — then update the docstring to say the two are now separately calibrated and why.

- [ ] **Step 4: Pin the result**

```python
# append to tests/test_shopping_templates.py
def test_the_buy_point_lands_inside_the_matched_row(cache) -> None:
    """A tap outside the tile hits whatever is behind it - on this page, a
    different upgrade."""
    screen = frame("menu_workshop_attack")
    template = cache.get(config.WORKSHOP_ROWS["Damage"])
    match = vision.locate_template(screen, template, 0.9)
    x, y = config.workshop_buy_point(match.top_left)
    left, top = match.top_left
    height, width = template.shape[:2]
    assert left <= x <= left + width * 3, "buy point is outside the row's tile"
    assert top <= y <= top + height * 2
```

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_shopping_templates.py tests/test_shopping.py -q`
Expected: PASS.

```bash
git add config.py shopping.py tests/test_shopping_templates.py
git commit -m "feat: calibrate the workshop buy point against the live device

Derived from ROW_PRICE_REGION rather than measured twice, matching how
buy_point() works in-run - the same trap applies: a row's label is a button
that opens an info panel, and a tap there buys nothing and blinds the next
scan.

Settled by one watched tap on the cheapest row rather than by guessing."
```

---

### Task 14: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the sections**

Add a **Shopping between runs** section after "Auto-navigation" covering: what a visit is and that it is one step per scan; that unlike in-run upgrades this spends coins and gems, which are permanent; the `enabled`/`armed` split and that armed defaults false; the buy rule (highest-priority affordable row, spend down, tab order derived from row order); the gem floor and per-visit caps; and what it never taps — slots, labs, modules, relics, the shop, and UW selection.

Update the **Screens, gating, and runs** section: menu pages are classified by `pages.py` over `PAGE_ANCHORS`, deliberately separate from `ScreenState`, and unknown-screen snapshotting is suppressed during a visit.

Update the **dashboard** section's page list with **Guide**, and the Strategy bullet with the Shopping section.

Extend the **security** warning: the dashboard can now arm spending and rewrite the buy list, which is the first control whose damage survives the process exiting.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document shopping between runs

Including the part that matters most: this is the first thing the bot does
that spends a currency you cannot get back, and armed defaults to false."
```

---

## Self-Review

**Spec coverage.** §3 buying rule → Tasks 6, 8. §4 control flow and the three collisions → Tasks 8, 9. §5 vision, templates, atlas, buy point → Tasks 1, 2, 3, 5, 13. §6 schema → Task 6. §7 default order → Task 10. §8 events and storage → Task 7. §9 dashboard → Tasks 11, 12. §10 Guide → Task 11. §11 security → Task 14. §12 risks → mitigations land in Tasks 5 (dynamic layout goldens), 8 (bail-outs), 13 (buy point). §13 testing → every task. §14 order → Tasks 1–13 in order.

One spec item is deliberately deferred rather than dropped: §9's "Live page shows an active visit inline". The events from Task 7 already reach the feed, so a visit is legible without it; the inline panel is a follow-up and is not in this plan.

**Placeholder scan.** No TBDs. Two steps intentionally require live device work with no code to pre-write — Task 3 (harvesting glyphs) and Task 13 (the watched tap) — and both give the exact commands, the acceptance test, and what to do when it fails.

**Type consistency.** `ShoppingRule`, `CardPolicy`, `Shopping` carry identical field names in `strategy.py` (Task 6), `types.ts` (Task 12) and the JSON in `strategies/default.json` (Task 10). `Shopping.rows_for` and `categories_in_priority_order` are defined in Task 6 and consumed in Task 8. `digits.threshold_for` (Task 1) is consumed in Tasks 2 and 3. `pages.classify_page` returning `PageReading` (Task 4) is consumed in Task 8. `config.ROW_PRICE_REGION` (Task 5) is consumed in Tasks 8 and 13. `build_shopping` (Task 9) is the only place the header atlas gate lives.
