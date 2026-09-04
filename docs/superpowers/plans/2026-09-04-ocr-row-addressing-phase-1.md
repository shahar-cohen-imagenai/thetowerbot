# OCR Row Addressing — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read upgrade names and prices off the screen with OCR, running as a
silent observer alongside the existing template matcher, so the two readers
can be compared on live prices before a single coin is risked.

**Architecture:** Two new modules. `ocr.py` turns pixels into text boxes and
owns the engine, the confidence floor and the number parse. `tiles.py`
detects the bordered upgrade tiles and assigns text boxes to them, producing
a `Row` with a name, a price and a tap point. `shopping.py` calls the new
path alongside the old one and logs where they disagree — **the template
path still makes every decision in Phase 1.** No behaviour changes.

**Tech Stack:** Python 3.12, OpenCV, RapidOCR (ONNX), pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-ocr-row-addressing-design.md`

## Global Constraints

- Emulator resolution is **1080x2400**. Every fixture is at that size and
  `cv2.matchTemplate` is not scale-invariant.
- **`config.OCR_CONFIDENCE_FLOOR = 0.85`** — boxes below it are dropped
  inside `ocr.py` and never reach a consumer.
- Number parse accepts **`$?digits[.digits][KMB]` and nothing else**.
  `68 $` must return `None`.
- Pin **`rapidocr-onnxruntime==1.4.4`**.
- Every reader returns empty/`None` rather than raising. A bad read degrades
  the bot; it never stops the scan loop.
- Tests assert text and confidence **thresholds**, never exact confidence
  values.
- Run tests with `-p no:allure_pytest` and an explicit long Bash timeout.
- **Phase 1 removes nothing.** No template, config constant or schema field
  is deleted; that is Phase 2's job.

## File Structure

| File | Responsibility |
|---|---|
| `ocr.py` (create) | Pixels → `TextBox`. Engine singleton + lock, confidence floor, number parse. |
| `tiles.py` (create) | `TextBox` + pixels → `Row`. Tile detection, box-to-tile assignment, name normalisation. |
| `tools/tile_preview.py` (create) | Measurement tool — renders detected tiles onto a frame. Mirrors `tools/crop_preview.py`. |
| `tests/test_tiles.py` (create) | Tile detection and row assembly. Runs without the OCR engine, from recorded JSON. |
| `tests/test_ocr.py` (create) | Number parse (no engine) plus a few real-engine reads. |
| `tests/fixtures/ocr/*.json` (create) | Recorded `TextBox` output per fixture, so `tiles.py` tests need no engine. |
| `shopping.py` (modify) | Call the new path alongside the old; log disagreements. |
| `config.py` (modify) | `OCR_CONFIDENCE_FLOOR`, tile size bounds, label/price proportions. |
| `pyproject.toml` (modify) | Add the pinned dependency. |

---

### Task 1: Tile detection, and the go/no-go gate

The whole design rests on separating **outer** tiles from the value and
price panels nested inside them. That is unvalidated. If this task cannot be
made to pass, **stop and revisit the spec** — the measured-grid alternative
in spec §3 is the fallback and the design changes.

**Files:**
- Create: `tiles.py`
- Create: `tools/tile_preview.py`
- Create: `tests/test_tiles.py`
- Modify: `config.py` (append the tile constants)

**Interfaces:**
- Consumes: nothing.
- Produces: `config.Rect(x, y, w, h)` — an **absolute** frame rectangle, not
  a `config.Region` (which is anchor-relative `dx, dy, w, h`; conflating
  them applies an offset twice). It lives in `config.py` beside `Region` and
  `Action` because **both** `ocr.py` and `tiles.py` need it, and defining it
  in either one would make them import each other. `tiles.py` re-exports it,
  so `tiles.Rect` is valid.
  `tiles.find_tiles(image) -> tuple[Rect, ...]`.

- [ ] **Step 1: Write the measurement tool**

You cannot assert rectangles you have not measured. This tool is how
`PRICE_REGIONS` was measured (`tools/crop_preview.py`), so it follows the
same shape.

Create `tools/tile_preview.py`:

```python
"""Render the tile rectangles find_tiles() detects onto a frame.

Usage:
    uv run tools/tile_preview.py tests/fixtures/menu_workshop_attack.png \
        --out /tmp/tiles.png

Prints every candidate rectangle with its size so you can see which are
outer tiles and which are the value/price panels nested inside them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tiles  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frame", type=Path)
    parser.add_argument("--out", type=Path, default=Path("/tmp/tiles.png"))
    parser.add_argument(
        "--all", action="store_true",
        help="show every candidate, before the size filter",
    )
    args = parser.parse_args(argv)

    screen = cv2.imread(str(args.frame))
    if screen is None:
        print(f"cannot read {args.frame}", file=sys.stderr)
        return 1

    found = tiles.candidates(screen) if args.all else tiles.find_tiles(screen)
    for rect in found:
        print(f"x={rect.x:4d} y={rect.y:4d} w={rect.w:4d} h={rect.h:4d}")
    print(f"{len(found)} rectangles")

    for rect in found:
        cv2.rectangle(
            screen, (rect.x, rect.y),
            (rect.x + rect.w, rect.y + rect.h), (0, 0, 255), 3,
        )
    cv2.imwrite(str(args.out), screen)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write the failing test**

The test does **not** hard-code rectangles. It asserts the two properties
that actually matter, using anchors already measured by template match:
every known upgrade sits inside exactly one detected tile, and no tile
swallows two of them. Nested panels leaking in would raise the count;
merged tiles would lower it.

Create `tests/test_tiles.py`:

```python
"""Tile detection against the committed fixtures."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import tiles

FIXTURES = Path(__file__).parent / "fixtures"

# Anchors measured by template match (spec §1, "What the screen actually
# looks like"). A point known to be inside each upgrade's tile.
ATTACK = [
    ("Damage", 130, 560),
    ("Attack Speed", 645, 560),
    ("Critical Chance", 130, 770),
    ("Critical Factor", 645, 770),
    ("Unlock Range Upgrades", 540, 964),
]
DEFENSE = [
    ("Damage-slot Health", 130, 560),
    ("Health Regen", 645, 560),
    ("Unlock Defense Upgrades", 541, 754),
]
UTILITY = [("Unlock Cash Bonuses", 541, 544)]
IN_RUN = [
    ("Damage", 130, 1840),
    ("Attack Speed", 645, 1840),
    ("Critical Chance", 122, 2040),
    ("Critical Factor", 639, 2040),
]

CASES = [
    ("menu_workshop_attack.png", ATTACK),
    ("menu_workshop_defense.png", DEFENSE),
    ("menu_workshop_utility.png", UTILITY),
    ("in_run_lit.png", IN_RUN),
]


def _contains(rect: tiles.Rect, x: int, y: int) -> bool:
    return rect.x <= x < rect.x + rect.w and rect.y <= y < rect.y + rect.h


@pytest.mark.parametrize("fixture,anchors", CASES)
def test_every_upgrade_lands_in_exactly_one_tile(fixture, anchors):
    screen = cv2.imread(str(FIXTURES / fixture))
    found = tiles.find_tiles(screen)
    for label, x, y in anchors:
        hits = [r for r in found if _contains(r, x, y)]
        assert len(hits) == 1, f"{label} landed in {len(hits)} tiles: {hits}"


@pytest.mark.parametrize("fixture,anchors", CASES)
def test_no_tile_swallows_two_upgrades(fixture, anchors):
    screen = cv2.imread(str(FIXTURES / fixture))
    for rect in tiles.find_tiles(screen):
        inside = [n for n, x, y in anchors if _contains(rect, x, y)]
        assert len(inside) <= 1, f"{rect} contains {inside}"


@pytest.mark.parametrize("fixture,anchors", CASES)
def test_nested_panels_are_filtered_out(fixture, anchors):
    """One tile per upgrade and nothing else.

    Each tile contains a value panel and a price panel, themselves bordered
    rectangles. If the size filter lets those through, the count rises above
    the number of upgrades on the page.
    """
    screen = cv2.imread(str(FIXTURES / fixture))
    assert len(tiles.find_tiles(screen)) == len(anchors)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_tiles.py -q -p no:allure_pytest`
Expected: FAIL — `ModuleNotFoundError: No module named 'tiles'`

- [ ] **Step 4: Write the implementation**

`RETR_EXTERNAL` returns only outermost contours, which is what discards the
nested panels; the size filter is the belt to that braces.

Create `tiles.py`:

```python
"""Find the upgrade tiles on a page, and what is written in them.

A tile is a bright bordered rectangle. Everything the bot may tap lives
inside one, so detecting tiles is what lets an upgrade be addressed by the
name written on it rather than by a template cut from it.
"""

from __future__ import annotations

import logging

import cv2

import config
from config import Rect  # re-exported: tiles.Rect is the name callers use
from device import Image

logger = logging.getLogger("tower_bot.tiles")


def _contains(rect: Rect, x: int, y: int) -> bool:
    return rect.x <= x < rect.x + rect.w and rect.y <= y < rect.y + rect.h


def candidates(screen: Image) -> tuple[Rect, ...]:
    """Every outermost bordered rectangle, before the size filter.

    Exposed for tools/tile_preview.py: seeing what the filter rejected is
    how the bounds in config get chosen.
    """
    grey = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(grey, config.TILE_BINARY_THRESHOLD, 255, cv2.THRESH_BINARY)
    # RETR_EXTERNAL, not RETR_LIST: a tile's value and price panels are
    # bordered rectangles INSIDE the tile, and external-only is what drops
    # them. The size filter below is the second line of defence, not the
    # first.
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return tuple(Rect(*cv2.boundingRect(c)) for c in contours)


def find_tiles(screen: Image) -> tuple[Rect, ...]:
    """The upgrade tiles, top to bottom then left to right."""
    kept = [
        rect
        for rect in candidates(screen)
        if config.TILE_MIN_W <= rect.w <= config.TILE_MAX_W
        and config.TILE_MIN_H <= rect.h <= config.TILE_MAX_H
    ]
    # Reading order, so a caller can rely on priority-by-position without
    # sorting again.
    return tuple(sorted(kept, key=lambda r: (r.y, r.x)))
```

Append to `config.py`:

```python
# --- Upgrade tiles --------------------------------------------------------
# An absolute rectangle on the frame. Not Region, which is anchor-relative
# (dx, dy): a Rect says where something IS, a Region says where to look
# relative to something already found. Both ocr.py and tiles.py need this,
# which is why it lives here rather than in either of them.
class Rect(NamedTuple):
    x: int
    y: int
    w: int
    h: int


# A tile is a bright bordered rectangle: a half-width upgrade tile, or a
# full-width unlock tile. Both the workshop and the in-run panel use them.
#
# STARTING VALUES ONLY - replace these with what tools/tile_preview.py
# actually measures (Task 1, step 5) and rewrite this comment to record it,
# the way PRICE_REGIONS records what was measured when its crops were cut.
# The bounds want to be wide: they exist to reject the value and price
# panels nested inside a tile, not to pin a layout a game update may nudge.
TILE_BINARY_THRESHOLD: int = 100
TILE_MIN_W: int = 400
TILE_MAX_W: int = 1060
TILE_MIN_H: int = 60
TILE_MAX_H: int = 260
```

`NamedTuple` needs `from typing import NamedTuple` in `config.py` — check
whether it is already imported before adding it.

- [ ] **Step 5: Measure, and tune the bounds against real output**

Run the tool on each fixture and read the numbers:

```bash
uv run tools/tile_preview.py tests/fixtures/menu_workshop_attack.png --all --out /tmp/all.png
uv run tools/tile_preview.py tests/fixtures/menu_workshop_attack.png --out /tmp/kept.png
```

Open both PNGs. `--all` shows what `RETR_EXTERNAL` returned; the second
shows what survived the filter. Adjust `TILE_BINARY_THRESHOLD` and the four
bounds in `config.py` until the kept set is exactly the upgrade tiles, then
rewrite the `STARTING VALUES ONLY` comment to record what you measured.

Repeat for all four fixtures. **`in_run_lit.png` is the one expected to give
trouble**: its background is a live battle rather than a flat menu, and the
frame carries HUD panels above the upgrade area that may well be bordered
rectangles in the right size range. If size bounds alone cannot separate
them, the fix is to restrict detection to the upgrade panel — pass an
optional `region: Rect | None` to `find_tiles` and crop before detecting,
rather than widening the bounds until HUD elements slip through. Record that
region in `config.py` the same way.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tiles.py -q -p no:allure_pytest`
Expected: PASS, 12 tests (3 tests x 4 fixtures)

**If you cannot make these pass by tuning:** stop. Do not proceed to Task 2.
Report which fixture fails and how (nested panels leaking, tiles merging, or
tiles not detected at all), and hand back to the spec — §3's measured-grid
alternative is the fallback.

- [ ] **Step 7: Commit**

```bash
git add tiles.py tools/tile_preview.py tests/test_tiles.py config.py
git commit -m "feat: detect upgrade tiles as bordered rectangles

RETR_EXTERNAL drops the value and price panels nested inside each tile; the
size bounds in config are the second line of defence. Tests assert the two
properties that matter - every known upgrade lands in exactly one tile, and
no tile swallows two - rather than hard-coding rectangles."
```

---

### Task 2: `ocr.py` — engine, confidence floor, number parse

**Files:**
- Create: `ocr.py`
- Create: `tests/test_ocr.py`
- Modify: `pyproject.toml`
- Modify: `config.py` (append `OCR_CONFIDENCE_FLOOR`)

**Interfaces:**
- Consumes: `config.Rect` from Task 1.
- Produces: `ocr.TextBox(text: str, confidence: float, rect: config.Rect)`,
  `ocr.read(image) -> tuple[TextBox, ...]`,
  `ocr.parse_number(text: str) -> int | None`.

**No startup gate in Phase 1.** Spec §10 requires one, but it belongs with
the cut-over: while OCR only observes, a missing engine costs a comparison,
not a purchase. Adding `ocr.available()` now would be dead code. It is
listed in the Phase 2 outline.

- [ ] **Step 1: Add the pinned dependency**

In `pyproject.toml`, under `dependencies`:

```toml
    # Reads upgrade names and prices off the screen. Pinned: RapidOCR's
    # output can shift between versions, and the confidence floor in
    # config.py is calibrated against this one.
    "rapidocr-onnxruntime==1.4.4",
```

Then: `uv sync`

- [ ] **Step 2: Write the failing test**

Most of these need no engine, which is why the parse is a separate function
rather than buried inside `read`.

Create `tests/test_ocr.py`:

```python
"""The OCR reader: number parsing, and a few real reads."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import config
import ocr

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("30", 30),
        ("$10", 10),
        ("1.77K", 1770),
        ("2M", 2_000_000),
        ("0", 0),
    ],
)
def test_parses_a_number(text, expected):
    assert ocr.parse_number(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "68 $",        # symbol on the wrong side - seen on the in-run wallet
        "Damage",
        "",
        "1.2.3",
        "50 coins",
        "x1.20",       # a stat value, not a price
        "0.00/sec",
    ],
)
def test_refuses_anything_else(text):
    """Refuse rather than guess: a partial parse of a stat value would
    report a wrong price, which is the failure this whole rule prevents."""
    assert ocr.parse_number(text) is None


def test_reads_the_workshop_labels():
    """Real engine, real fixture. Slow - kept to one test on purpose."""
    screen = cv2.imread(str(FIXTURES / "menu_workshop_attack.png"))
    found = {box.text for box in ocr.read(screen)}
    for label in ("Damage", "Critical", "Chance", "Factor", "Unlock Range Upgrades"):
        assert label in found


def test_drops_boxes_below_the_confidence_floor():
    screen = cv2.imread(str(FIXTURES / "menu_workshop_utility.png"))
    for box in ocr.read(screen):
        assert box.confidence >= config.OCR_CONFIDENCE_FLOOR


def test_a_frame_it_cannot_read_returns_empty_not_an_exception():
    assert ocr.read(None) == ()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_ocr.py -q -p no:allure_pytest`
Expected: FAIL — `ModuleNotFoundError: No module named 'ocr'`

- [ ] **Step 4: Write the implementation**

Create `ocr.py`:

```python
"""Read text off the screen.

    frame  ->  engine  ->  drop low confidence  ->  TextBox

Every entry point returns empty or None rather than raising. A bad read must
degrade the bot, never stop the scan loop - the same rule digits.py follows.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

import config
from config import Rect
from device import Image

logger = logging.getLogger("tower_bot.ocr")

_engine: Any | None = None
# One lock guarding construction AND inference. The bot and the web server
# share a process, and the library makes no thread-safety promise worth
# betting a purchase on.
_lock = threading.Lock()


@dataclass(frozen=True)
class TextBox:
    text: str
    confidence: float
    rect: Rect


def _engine_or_none() -> Any | None:
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
        except Exception:
            logger.exception("could not build the OCR engine")
            return None
    return _engine


def read(screen: Image | None) -> tuple[TextBox, ...]:
    """Every text box on `screen` that clears the confidence floor."""
    if screen is None:
        return ()
    with _lock:
        engine = _engine_or_none()
        if engine is None:
            return ()
        try:
            result, _elapsed = engine(screen)
        except Exception:
            logger.exception("OCR failed on a %s frame", getattr(screen, "shape", "?"))
            return ()

    boxes: list[TextBox] = []
    for box, text, confidence in result or ():
        if confidence < config.OCR_CONFIDENCE_FLOOR:
            logger.debug("dropped %r at confidence %.3f", text, confidence)
            continue
        xs = [int(point[0]) for point in box]
        ys = [int(point[1]) for point in box]
        boxes.append(
            TextBox(
                text=text.strip(),
                confidence=float(confidence),
                rect=Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)),
            )
        )
    return tuple(boxes)


# Anchored at both ends: a partial match on "0.00/sec" or "x1.20" would
# report a stat value as a price. Refusing is always safe - the row is
# skipped as unreadable - while a wrong number is not.
_NUMBER = re.compile(r"^\$?(\d+(?:\.\d+)?)([KMB])?$")
_SUFFIXES = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_number(text: str) -> int | None:
    """A price or balance, or None if this is not one."""
    match = _NUMBER.fullmatch(text.strip())
    if match is None:
        return None
    digits, suffix = match.groups()
    return int(float(digits) * _SUFFIXES.get(suffix or "", 1))
```

Append to `config.py`:

```python
# Boxes below this are dropped inside ocr.py and never reach a consumer.
# Measured on the committed fixtures: real content read at 0.968-1.000 and
# the one observed phantom - an 'A' on empty screen - at 0.555. This sits in
# the middle of that gap, the same pick-the-middle-of-the-plateau convention
# DIGIT_BINARY_THRESHOLD uses. Calibrated on four images; revisit against
# live data.
OCR_CONFIDENCE_FLOOR: float = 0.85
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ocr.py -q -p no:allure_pytest`
Expected: PASS, 17 tests. The two engine tests take a few seconds on first
run while the ONNX models load.

- [ ] **Step 6: Commit**

```bash
git add ocr.py tests/test_ocr.py config.py pyproject.toml uv.lock
git commit -m "feat: read screen text with RapidOCR behind a confidence floor

parse_number is anchored at both ends so a stat value like '0.00/sec' or a
wallet rendered '68 \$' refuses rather than partially parsing into a wrong
price. The engine is a locked singleton: the bot and web server share a
process and the library makes no thread-safety promise."
```

---

### Task 3: `tiles.read_rows` — boxes into named rows

**Files:**
- Modify: `tiles.py`
- Modify: `tests/test_tiles.py`
- Create: `tests/fixtures/ocr/menu_workshop_attack.json` (and three siblings)
- Create: `tools/record_ocr.py`

**Interfaces:**
- Consumes: `ocr.TextBox`, `ocr.parse_number`, `tiles.find_tiles`.
- Produces: `tiles.Row(name, price, tap, rect, confidence)`,
  `tiles.read_rows(image) -> tuple[Row, ...]`,
  `tiles.normalise(text) -> str`,
  `tiles.rows_from(boxes, tiles_) -> tuple[Row, ...]` — the pure function the
  tests drive, so they need no engine.

- [ ] **Step 1: Write the recorder**

Recording OCR output once keeps the row-assembly tests instant and
engine-free — the reason `ocr.py` and `tiles.py` are separate modules.

Create `tools/record_ocr.py`:

```python
"""Record ocr.read() output for a fixture, so tiles tests need no engine.

    uv run tools/record_ocr.py tests/fixtures/menu_workshop_attack.png
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ocr  # noqa: E402


def main(argv: list[str]) -> int:
    for name in argv:
        source = Path(name)
        screen = cv2.imread(str(source))
        boxes = [asdict(box) for box in ocr.read(screen)]
        out = source.parent / "ocr" / f"{source.stem}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(boxes, indent=2) + "\n")
        print(f"{out}: {len(boxes)} boxes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

Run it for all four fixtures:

```bash
uv run tools/record_ocr.py \
    tests/fixtures/menu_workshop_attack.png \
    tests/fixtures/menu_workshop_defense.png \
    tests/fixtures/menu_workshop_utility.png \
    tests/fixtures/in_run_lit.png
```

- [ ] **Step 2: Write the failing test**

These are the two cases the spike proved hard. Append to
`tests/test_tiles.py` — the two imports go with the existing imports at the
top of the file, not mid-file:

```python
import json

import ocr


def _recorded(stem: str) -> tuple[ocr.TextBox, ...]:
    raw = json.loads((FIXTURES / "ocr" / f"{stem}.json").read_text())
    return tuple(
        ocr.TextBox(
            text=entry["text"],
            confidence=entry["confidence"],
            rect=tiles.Rect(*entry["rect"]),
        )
        for entry in raw
    )


def _rows(stem: str, fixture: str) -> dict[str, tiles.Row]:
    screen = cv2.imread(str(FIXTURES / fixture))
    found = tiles.rows_from(_recorded(stem), tiles.find_tiles(screen))
    return {row.name: row for row in found}


def test_a_wrapped_label_joins_into_one_row():
    """'Critical' and 'Chance' are separate OCR boxes 50px apart."""
    rows = _rows("menu_workshop_attack", "menu_workshop_attack.png")
    assert "Critical Chance" in rows
    assert "Critical Factor" in rows


def test_a_price_binds_to_its_own_tile_not_the_neighbour():
    """'30' at x=435 is Damage's price; 'Attack' at x=643 is the next
    tile's label. The boundary between them is at x~541."""
    rows = _rows("menu_workshop_attack", "menu_workshop_attack.png")
    assert rows["Damage"].price == 30
    assert rows["Attack Speed"].price == 30
    assert rows["Critical Chance"].price == 50
    assert rows["Critical Factor"].price == 50


def test_a_stat_value_is_not_mistaken_for_a_price():
    """Damage's value box reads '3' and sits above the price box."""
    rows = _rows("menu_workshop_attack", "menu_workshop_attack.png")
    assert rows["Damage"].price == 30


def test_reads_the_unlock_tile():
    rows = _rows("menu_workshop_utility", "menu_workshop_utility.png")
    assert rows["Unlock Cash Bonuses"].price == 40


def test_in_run_rows_read_their_dollar_prices():
    rows = _rows("in_run_lit", "in_run_lit.png")
    assert rows["Damage"].price == 10
    assert rows["Attack Speed"].price == 5
    assert rows["Critical Chance"].price == 4


def test_the_tap_point_is_inside_the_tile():
    rows = _rows("menu_workshop_attack", "menu_workshop_attack.png")
    for row in rows.values():
        assert _contains(row.rect, *row.tap)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Attack Speed", "attackspeed"),
        ("ATTACKUPGRADES", "attackupgrades"),   # all-caps drops the space
        ("Coins/Kill Bonus", "coinskillbonus"),
        ("  Damage  ", "damage"),
    ],
)
def test_normalise(raw, expected):
    assert tiles.normalise(raw) == expected


def test_normalise_does_not_make_different_rows_equal():
    assert tiles.normalise("Critical Chance") != tiles.normalise("Critical Factor")
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_tiles.py -q -p no:allure_pytest`
Expected: FAIL — `AttributeError: module 'tiles' has no attribute 'rows_from'`

- [ ] **Step 4: Write the implementation**

Append to `tiles.py`:

```python
import re

import ocr


@dataclass(frozen=True)
class Row:
    """One upgrade the bot can see, and everything needed to buy it.

    `name` is the RAW text as read, not the normalised form: it is what an
    event reports when nothing matched, and "read 'Criticai Chance'" is only
    useful if it says what was actually on the screen. Matching normalises
    both sides at comparison time instead.
    """

    name: str
    price: int | None
    tap: tuple[int, int]
    rect: Rect
    confidence: float


def normalise(text: str) -> str:
    """Case-fold and strip everything that is not a letter or digit.

    All-caps text loses its spaces in OCR ('ATTACKUPGRADES'), so comparing
    raw strings would refuse a row that was read perfectly well. Stripping
    punctuation also makes 'Coins/Kill Bonus' survive a missing slash.
    """
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _centre(rect: Rect) -> tuple[int, int]:
    return rect.x + rect.w // 2, rect.y + rect.h // 2


def rows_from(boxes: tuple[ocr.TextBox, ...], found: tuple[Rect, ...]) -> tuple[Row, ...]:
    """Assign text boxes to tiles, then read each tile as one row.

    Pure, and separate from read_rows(), so the tests can drive it from
    recorded JSON without loading the OCR engine.
    """
    rows: list[Row] = []
    for tile in found:
        inside = [box for box in boxes if _contains(tile, *_centre(box.rect))]
        if not inside:
            continue

        # A full-width unlock tile centres its label, so position alone
        # cannot split label from price. The split is by "does this parse as
        # a number" first - a box that parses as one is never part of a name
        # - and by config.TILE_PRICE_TOP_FRACTION second, which is what
        # keeps a stat value ('3') from being read as the price ('30').
        label_boxes = [
            box for box in inside
            if ocr.parse_number(box.text) is None
        ]
        price_boxes = [
            box for box in inside
            if ocr.parse_number(box.text) is not None
            and box.rect.y >= tile.y + tile.h * config.TILE_PRICE_TOP_FRACTION
        ]

        if not label_boxes:
            continue
        # Top-to-bottom, then left-to-right: a wrapped label reads down.
        label_boxes.sort(key=lambda b: (b.rect.y, b.rect.x))
        name = " ".join(box.text for box in label_boxes)

        # Lowest price box wins: the value panel sits above the price panel,
        # and a stat value that happens to parse as a number ('3') would
        # otherwise be read as the price.
        price = None
        if price_boxes:
            price_boxes.sort(key=lambda b: b.rect.y)
            price = ocr.parse_number(price_boxes[-1].text)

        rows.append(
            Row(
                name=name,
                price=price,
                tap=_centre(tile),
                rect=tile,
                confidence=min(box.confidence for box in label_boxes),
            )
        )
    return tuple(rows)


def read_rows(screen: Image) -> tuple[Row, ...]:
    """Every upgrade visible on `screen`."""
    return rows_from(ocr.read(screen), find_tiles(screen))
```

`_contains` already exists in `tiles.py` from Task 1.

Append to `config.py`:

```python
# Where the price sits within a tile, as a fraction of tile height. A tile
# holds a stat value panel above a price panel, and both parse as numbers -
# so position is what separates "3" (Damage's level) from "30" (its price).
# Measured with tools/tile_preview.py against the committed fixtures.
TILE_PRICE_TOP_FRACTION: float = 0.55
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tiles.py -q -p no:allure_pytest`
Expected: PASS, all tests including the 12 from Task 1.

If `test_a_stat_value_is_not_mistaken_for_a_price` fails, tune
`TILE_PRICE_TOP_FRACTION` using `tools/tile_preview.py` to see where the
panels sit, and update the comment with what you measured.

- [ ] **Step 6: Commit**

```bash
git add tiles.py tests/test_tiles.py tests/fixtures/ocr tools/record_ocr.py config.py
git commit -m "feat: assemble OCR boxes into named upgrade rows

Row.name keeps the raw text rather than the normalised form, so an event
that matched nothing can report what was actually on screen. rows_from() is
pure and driven from recorded JSON, so row-assembly tests need no engine."
```

---

### Task 4: Run OCR alongside the template path and log disagreements

Phase 1 changes no behaviour. The template path still decides every
purchase; OCR reads the same tile and the two are compared. This is the
rehearsal that earns the right to delete the templates in Phase 2.

**Files:**
- Modify: `shopping.py:525-585` (`_buy_rows`)
- Create: `tests/test_ocr_comparison.py`

**Interfaces:**
- Consumes: `tiles.read_rows`, `tiles.normalise`, `tiles.Row`.
- Produces: `shopping.compare_readers(rule, template_price: int | None,
  rows: tuple[tiles.Row, ...]) -> str | None` — the disagreement
  description, or None when the two agree. Pure, so it is testable without a
  device.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ocr_comparison.py`:

```python
"""The Phase 1 A/B: does OCR agree with the template reader?

Throwaway scaffolding. Deleted in Phase 2 along with the template path.
"""

from __future__ import annotations

import shopping
import tiles
from strategy import ShoppingRule

RULE = ShoppingRule(
    name="Damage",
    template="workshop/row_damage.png",
    category="ATTACK",
    layout="row",
)


def _row(name: str, price: int | None) -> tiles.Row:
    return tiles.Row(
        name=name, price=price, tap=(130, 560),
        rect=tiles.Rect(30, 478, 505, 210), confidence=0.99,
    )


def test_agreement_is_silent():
    assert shopping.compare_readers(RULE, 30, (_row("Damage", 30),)) is None


def test_a_different_price_is_reported():
    note = shopping.compare_readers(RULE, 30, (_row("Damage", 50),))
    assert note is not None
    assert "30" in note and "50" in note


def test_a_row_ocr_could_not_find_is_reported():
    note = shopping.compare_readers(RULE, 30, (_row("Health", 30),))
    assert note is not None
    assert "Health" in note, "the note must say what WAS read"


def test_an_unreadable_ocr_price_is_reported():
    note = shopping.compare_readers(RULE, 30, (_row("Damage", None),))
    assert note is not None


def test_normalisation_differences_are_not_disagreements():
    assert shopping.compare_readers(RULE, 30, (_row("DAMAGE", 30),)) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ocr_comparison.py -q -p no:allure_pytest`
Expected: FAIL — `AttributeError: module 'shopping' has no attribute 'compare_readers'`

- [ ] **Step 3: Write the implementation**

Add to `shopping.py`, above `ShoppingSession`:

```python
# --- Phase 1 A/B scaffolding ---------------------------------------------
# THROWAWAY. Exists to compare the OCR reader against the template reader on
# live prices before any coin is risked on the former. Deleted in Phase 2
# along with the template path itself - see the OCR row addressing spec §12.
ab_logger = logging.getLogger("tower_bot.ocr_ab")


def compare_readers(
    rule: Any, template_price: int | None, rows: tuple[tiles.Row, ...]
) -> str | None:
    """Describe how the two readers disagree about `rule`, or None.

    Compares on the normalised name, so a difference of case or spacing is
    not reported as a disagreement - only a different row or a different
    number is.
    """
    wanted = tiles.normalise(rule.name)
    seen = [row for row in rows if tiles.normalise(row.name) == wanted]
    if not seen:
        read = ", ".join(repr(row.name) for row in rows) or "nothing"
        return f"{rule.name}: OCR did not find it; read {read}"
    ocr_price = seen[0].price
    if ocr_price is None:
        return f"{rule.name}: template read {template_price}, OCR could not read a price"
    if ocr_price != template_price:
        return f"{rule.name}: template read {template_price}, OCR read {ocr_price}"
    return None
```

Then in `_buy_rows`, immediately after the existing price read succeeds
(after the `if price is None:` block, before the affordability check), add:

```python
        # Phase 1 A/B - observation only, never a decision. Deleted in
        # Phase 2. Wrapped because a reader under evaluation must not be
        # able to break the reader in production.
        try:
            note = compare_readers(rule, price, tiles.read_rows(screen))
            if note is not None:
                ab_logger.warning("%s", note)
            else:
                ab_logger.info("%s: readers agree on %s", rule.name, price)
        except Exception:
            ab_logger.exception("the OCR comparison itself failed")
```

Add `import tiles` to `shopping.py`'s imports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ocr_comparison.py -q -p no:allure_pytest`
Expected: PASS, 5 tests

- [ ] **Step 5: Verify nothing else broke**

`shopping.py` is the only module touched, so run its tests and the strategy
tests that construct shopping rules — not the whole suite.

Run: `uv run pytest tests/test_shopping.py tests/test_strategy_store.py -q -p no:allure_pytest`
Expected: PASS, unchanged from before this task.

- [ ] **Step 6: Commit**

```bash
git add shopping.py tests/test_ocr_comparison.py
git commit -m "feat: compare the OCR reader against the template reader

Observation only - the template path still decides every purchase. The
comparison is wrapped so a reader under evaluation cannot break the reader
in production, and is deleted in Phase 2 along with the template path."
```

---

## Running the rehearsal

Phase 1 is finished when the code is merged, but it has not *served its
purpose* until it has run against the live game. That is the gate on Phase 2.

1. Set the active strategy's `shopping.enabled = true` and leave
   `shopping.armed = false`. Nothing taps: the bot reads the workshop,
   compares the two readers, and buys nothing.

2. Run it **without `--tui`**, and redirect stderr to a file — the A/B is
   log-only (deliberately: giving it an event would mean schema churn in
   `events.py` and `db.py` for scaffolding Phase 2 deletes), `--tui`
   silences stdlib logging so rich can own the terminal, and
   `basicConfig` writes to stderr, which nothing captures on its own:

```bash
uv run tower_bot.py 2>&1 | tee rehearsal.log
```

3. **Advance the prices by hand.** This is the step that makes the
   rehearsal worth running, and it is manual work — there is no way around
   it. Prices escalate only when an upgrade is actually *bought*, and step 1
   guarantees the bot never buys anything, so a dry run left alone forever
   sees only the prices the account already has. The fixture-era prices
   (30/40/50/75) happen to contain none of the four digits the `menu` atlas
   is missing (`1`, `6`, `8`, `9` — see README, "The `menu` atlas is
   incomplete"), so a rehearsal at those prices can only ever report
   agreement, which proves nothing about the case Phase 2 rests on.

   So: **play the account manually while the dry run watches.** Buy
   workshop upgrades yourself — attack and defense rows both, and at least
   one unlock tile — until the visible prices contain each of `1`, `6`, `8`
   and `9`. The bot is reading the same screen you are; every visit it makes
   while you play produces a comparison line. (Any other way of moving the
   prices does as well — an account that is already further along, or a
   second armed session on a throwaway save. What does not work is leaving
   an unarmed bot running overnight.)

4. Read the comparison log. Each line carries its logger name, so `ocr_ab`
   is what selects the A/B; agreements are logged too, and are the noise
   here:

```bash
grep 'tower_bot.ocr_ab' rehearsal.log | grep -v 'readers agree'
```

   An empty result means one of two very different things — no
   disagreements, or no comparisons at all. Check which before reading it
   as a clean rehearsal:

```bash
grep -c 'tower_bot.ocr_ab' rehearsal.log   # must be well above zero
```

5. **Every disagreement must be explained before Phase 2 begins.** The
   line to hunt for is `the template reader could not read a price, OCR
   read N` — OCR right where the atlas refused, which is the argument for
   the cut-over. `neither reader could read a price` argues nothing either
   way. A disagreement where OCR produced a confident wrong number is a
   stop.
6. Capture scrolled fixtures during this session — Phase 2's map tests need
   them and none exist today.

---

## Phase 2 — outline only, deliberately

Phase 2 is **not** planned in bite-sized detail here, and that is a
deliberate choice rather than an omission.

Its tasks depend on evidence Phase 1 produces: the real confidence
distribution, whether the two readers agree, whether tile detection holds on
a live device rather than four fixtures, and scrolled captures that do not
yet exist. Writing detailed TDD steps now would be writing them against
guesses, and Task 1's gate can still invalidate the whole approach.

What Phase 2 contains, from spec §12:

- The startup gate spec §10 requires: `ocr.available()`, checked where
  `build_shopping()` checks the header atlas, disabling buying and
  publishing the reason when the engine will not load.
- Scroll-and-map: the `MAP_TAB` step, name-keyed dedupe, bottom detection,
  a swipe budget separate from `max_taps_per_visit`, and the truncated-map
  flag.
- In-run rolling map, `ActionRule.category`, and in-run tab switching.
- Schema cut-over: rows become `{name, category, enabled}`; `template`,
  `threshold`, `brightness_ratio`, `layout` and top-level `affordability`
  removed; `strategies/default.json` rewritten.
- Deletion of everything in spec §7, including this plan's Task 4
  scaffolding.

Write the Phase 2 plan after the rehearsal, from what it found.
