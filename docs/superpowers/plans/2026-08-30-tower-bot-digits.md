# Tower Bot Digits (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read the game's numbers — wallet, upgrade price, and the death modal's wave/coins/tier — so affordability becomes exact instead of a brightness guess, and so a run reports what it actually achieved.

**Architecture:** A new `digits.py` turns a screen region into an integer: threshold to binary, split into glyphs by column projection, match each glyph against a per-size-class atlas, concatenate, parse. Every region is expressed as an offset from a *matched* anchor, never an absolute coordinate, because the death modal shifts ~46px vertically. `DigitAffordability` implements the existing `AffordabilityCheck` protocol, so the swap is a one-line change in the bot, and it falls back to `BrightnessAffordability` whenever a read fails.

**Tech Stack:** Python 3.12, uv, adbutils, opencv-python, numpy, rich, pytest. **No new dependencies** — phase 3 adds none.

**Spec:** `docs/superpowers/specs/2026-08-30-tower-bot-observability-design.md` (sections 6, 4, 12, 13)

## Global Constraints

- Python >= 3.12. Every function gets type hints. `from __future__ import annotations` at the top of every module.
- Dependencies are managed with `uv add` / `uv add --dev` — never hand-edit `pyproject.toml`. Phase 3 should need neither.
- Emulator resolution is **1080x2400**. Templates and atlas glyphs are not scale-invariant; a different resolution invalidates every one of them.
- **No absolute coordinates for anything inside the death modal.** It shifts ~46px vertically depending on whether the "New Highest Wave!" line is present. Every region is an offset from a matched anchor's top-left.
- The full test suite must pass with **no emulator attached**. Device access is mocked; digit machinery is tested against committed fixtures and synthetically rendered glyphs.
- Existing thresholds stay put: `ANCHOR_THRESHOLD = 0.8`, `SCREEN_CONFIRMATIONS = 2`, `DEFAULT_BRIGHTNESS_RATIO = 0.75`.
- **Degrade, never crash.** Every digit read returns `int | None`. A failed read falls back to brightness affordability and leaves the event field `None`; it never raises into the scan loop.
- Run `uv run pytest -q` before every commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `digits.py` (new) | Region cropping, binarisation, glyph segmentation, atlas matching, number parsing, `NumberReader` |
| `tools/crop_preview.py` (new) | Calibration helper: render a candidate region against a fixture so a human can eyeball the crop |
| `build_atlas.py` (new) | Bootstrap: segment glyphs out of captured frames and dump unlabeled crops for one-time manual labelling |
| `config.py` (modify) | `Region` type, the measured region constants, atlas dir, digit thresholds |
| `affordability.py` (modify) | `DigitAffordability`; `last_price` / `last_wallet` on the protocol |
| `events.py` (modify) | `tier` on `RunEnded` |
| `tower_bot.py` (modify) | Read wallet per scan, enrich `Tapped` / `ScanCompleted` / `RunEnded`, `--affordability` flag |
| `templates/atlas/<size_class>/*.png` (new) | The labelled glyph atlas, one directory per size class |
| `tests/test_digits.py` (new) | Segmentation, atlas, parser, end-to-end read |
| `tests/test_digit_affordability.py` (new) | The gate and its fallback |

**Size classes.** The UI renders numbers at three sizes; `matchTemplate` is not scale-invariant, so each needs its own atlas: `wallet` (in-run HUD), `price` (upgrade buttons), `modal` (death modal stats).

---

### Task 1: Calibrate regions against real frames

This is the only task that needs a live emulator, and it is deliberately first: every later task consumes the constants it produces. It also closes the open item phase 2 left behind.

**Files:**
- Create: `tools/crop_preview.py`
- Create: `tests/fixtures/in_run_wallet.png`, `tests/fixtures/game_over_stats.png`
- Create (best effort): `tests/fixtures/in_run_greyed.png`
- Modify: `config.py`
- Modify: `docs/superpowers/specs/2026-08-30-tower-bot-observability-design.md` (section 13)
- Test: `tests/test_digits.py`

**Interfaces:**
- Consumes: `grab_screen.py` (existing), `vision.best_score`, `config.SCREEN_ANCHORS`
- Produces: `config.Region`, `config.WALLET_REGION`, `config.PRICE_REGION`, `config.MODAL_WAVE_REGION`, `config.MODAL_COINS_REGION`, `config.MODAL_TIER_REGION`, `config.ATLAS_DIR`, `config.DIGIT_BINARY_THRESHOLD`, `config.GLYPH_MATCH_THRESHOLD`, `config.GLYPH_MIN_WIDTH`

- [x] **Step 1: Add the `Region` type to `config.py`**

Append to the vision section of `config.py`, above `class Action`:

```python
class Region(NamedTuple):
    """A rectangle expressed relative to a matched anchor's top-left.

    Never absolute. The death modal shifts ~46px vertically depending on
    whether the "New Highest Wave!" line is present, so a hardcoded y would
    read the wrong row half the time. dx/dy may be negative: the anchor is
    not always above-left of the number it locates.
    """

    dx: int
    dy: int
    w: int
    h: int
```

- [x] **Step 2: Write the calibration helper**

Create `tools/crop_preview.py`:

```python
"""Eyeball a candidate Region against a captured frame.

Usage:
    uv run tools/crop_preview.py tests/fixtures/in_run_wallet.png \
        --anchor IN_RUN --rect 40 -60 220 46 --out /tmp/crop.png

Prints the anchor's match score and where it landed, writes the crop, and
prints the crop's mean grey level so you can tell a real number from an
empty patch of background.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

# tools/ is a subdirectory, so the repo root is not on sys.path when this is
# run directly. conftest.py does the same thing for the test suite.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import vision  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frame", type=Path, help="captured PNG to crop from")
    parser.add_argument(
        "--anchor", required=True, choices=sorted(config.SCREEN_ANCHORS),
        help="anchor the rect is measured from",
    )
    parser.add_argument(
        "--rect", nargs=4, type=int, metavar=("DX", "DY", "W", "H"), required=True,
        help="offsets from the anchor's top-left, then width and height",
    )
    parser.add_argument("--out", type=Path, default=Path("crop.png"))
    args = parser.parse_args(argv)

    screen = cv2.imread(str(args.frame), cv2.IMREAD_COLOR)
    if screen is None:
        raise SystemExit(f"could not read frame: {args.frame}")

    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    template = cache.get(config.SCREEN_ANCHORS[args.anchor])
    score, top_left = vision.best_score(screen, template)
    print(f"anchor {args.anchor}: score={score:.3f} top_left={top_left}")
    if score < config.ANCHOR_THRESHOLD:
        print(f"WARNING: below ANCHOR_THRESHOLD ({config.ANCHOR_THRESHOLD}) "
              "- the offsets below are measured from a bad origin")

    dx, dy, w, h = args.rect
    x, y = top_left[0] + dx, top_left[1] + dy
    crop = screen[y : y + h, x : x + w]
    if crop.size == 0:
        raise SystemExit(f"empty crop at ({x},{y}) {w}x{h} - rect is off-screen")

    cv2.imwrite(str(args.out), crop)
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).mean()
    print(f"crop {w}x{h} at absolute ({x},{y}) -> {args.out}  mean_grey={grey:.1f}")
    print(f"config.Region(dx={dx}, dy={dy}, w={w}, h={h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 3: Capture the two required frames**

With the emulator running and the game open:

```bash
# A live run, with the wallet and at least one upgrade price visible.
uv run grab_screen.py tests/fixtures/in_run_wallet.png

# Let the run end, then capture the death modal showing wave / coins / tier.
uv run grab_screen.py tests/fixtures/game_over_stats.png
```

Write the numbers you can read with your own eyes into a scratch note — wallet, one upgrade's price, wave, coins, tier. Later tests assert against them, so they must be the ground truth, not what the code says.

- [x] **Step 4: Capture the greyed-out frame and close the phase-2 open item**

This is the measurement phase 2 could not obtain. Start a run, spend the wallet down until an upgrade visibly greys out, then:

```bash
uv run grab_screen.py tests/fixtures/in_run_greyed.png
uv run tower_bot.py --debug-scores
```

Record the `brightness` column for the greyed action.

**If the greyed value is not comfortably below 0.75**, `BrightnessAffordability` never worked for its stated purpose, and this plan is load-bearing rather than an improvement. Either way, write the measured value into the spec's section 13 open item, replacing the "still uncaptured" paragraph with what you measured.

**If you cannot reproduce the greyed state** (phase 2 could not), do not stall: note that in the spec, skip the `in_run_greyed.png` fixture, and continue. Phase 3 removes the dependency regardless — that is the whole point of the task.

- [x] **Step 5: Measure the five regions**

For each region, iterate `crop_preview.py` until the written crop contains the number tightly, with a few pixels of margin and no neighbouring glyphs:

```bash
uv run tools/crop_preview.py tests/fixtures/in_run_wallet.png \
    --anchor IN_RUN --rect <dx> <dy> <w> <h> --out /tmp/wallet.png
open /tmp/wallet.png
```

Measure, in this order:

| Region | Frame | Anchor | Contains |
|---|---|---|---|
| `WALLET_REGION` | `in_run_wallet.png` | `IN_RUN` | the in-run cash total |
| `MODAL_WAVE_REGION` | `game_over_stats.png` | `GAME_OVER` | the wave number |
| `MODAL_COINS_REGION` | `game_over_stats.png` | `GAME_OVER` | coins earned |
| `MODAL_TIER_REGION` | `game_over_stats.png` | `GAME_OVER` | the tier number |

`PRICE_REGION` is different: it is measured from a **matched upgrade label**, not a screen anchor, because each of the four upgrade buttons has its own price box. Find the label's top-left first:

```bash
uv run python -c "
import cv2, config, vision
screen = cv2.imread('tests/fixtures/in_run_wallet.png')
cache = vision.TemplateCache(config.TEMPLATE_DIR)
tpl = cache.get('upgrade_damage.png')
m = vision.locate_template(screen, tpl, 0.9)
print('label top_left', m.top_left, 'size', tpl.shape[1], 'x', tpl.shape[0])
"
```

Then crop by hand from that origin with the same arithmetic (`x = top_left[0] + dx`), checking the result in an image viewer. The offset must work for **all four** upgrades — verify against each of `upgrade_damage.png`, `upgrade_attack_speed.png`, `upgrade_critical_chance.png`, `upgrade_critical_factor.png`. If one needs a different offset, the region belongs on `Action` rather than as a module constant; note that and raise it before proceeding.

- [x] **Step 6: Write the measured constants into `config.py`**

Append a new section. Replace every number below with what you actually measured — these are the values Step 5 printed, not guesses:

```python
# --- Digit reading --------------------------------------------------------
# Numbers are light glyphs on a dark panel. Binarise, split by column gaps,
# match each glyph against a per-size-class atlas.
ATLAS_DIR: Path = TEMPLATE_DIR / "atlas"

# Grey level above which a pixel counts as glyph rather than background.
DIGIT_BINARY_THRESHOLD: int = 140
# A glyph must match an atlas entry at least this well to be accepted.
GLYPH_MATCH_THRESHOLD: float = 0.7
# Narrowest run of lit columns still treated as a glyph. The decimal point is
# the narrowest real glyph, so raising this silently turns 1.5K into 15K.
GLYPH_MIN_WIDTH: int = 2

# Every region is relative to a matched anchor - see config.Region.
WALLET_REGION: Region = Region(dx=0, dy=0, w=0, h=0)        # from IN_RUN anchor
PRICE_REGION: Region = Region(dx=0, dy=0, w=0, h=0)         # from an upgrade label
MODAL_WAVE_REGION: Region = Region(dx=0, dy=0, w=0, h=0)    # from GAME_OVER anchor
MODAL_COINS_REGION: Region = Region(dx=0, dy=0, w=0, h=0)   # from GAME_OVER anchor
MODAL_TIER_REGION: Region = Region(dx=0, dy=0, w=0, h=0)    # from GAME_OVER anchor
```

- [x] **Step 7: Write the fixture guard test**

Create `tests/test_digits.py`:

```python
"""Digit reading. Fixtures are golden data - failures mean a capture drifted."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import config

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED_RESOLUTION = (1080, 2400)  # width, height


@pytest.mark.parametrize("name", ["in_run_wallet", "game_over_stats"])
def test_calibration_fixture_resolution(name: str) -> None:
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    height, width = img.shape[:2]
    assert (width, height) == EXPECTED_RESOLUTION


@pytest.mark.parametrize(
    "region",
    [
        "WALLET_REGION",
        "PRICE_REGION",
        "MODAL_WAVE_REGION",
        "MODAL_COINS_REGION",
        "MODAL_TIER_REGION",
    ],
)
def test_regions_were_calibrated(region: str) -> None:
    """The zeros in the plan are a placeholder; a measured region has size."""
    value = getattr(config, region)
    assert value.w > 0 and value.h > 0, f"{region} was never measured"
```

- [x] **Step 8: Run the tests**

Run: `uv run pytest tests/test_digits.py -v`
Expected: PASS. If `test_regions_were_calibrated` fails, Step 6 was skipped.

- [x] **Step 9: Commit**

```bash
git add config.py tools/crop_preview.py tests/test_digits.py \
    tests/fixtures/in_run_wallet.png tests/fixtures/game_over_stats.png \
    docs/superpowers/specs/2026-08-30-tower-bot-observability-design.md
git commit -m "feat: calibrate anchor-relative digit regions against live frames"
```

---

### Task 2: Crop a region from a screen

**Files:**
- Create: `digits.py`
- Test: `tests/test_digits.py`

**Interfaces:**
- Consumes: `config.Region`, `device.Image`
- Produces: `digits.crop(screen: Image, region: config.Region, anchor: tuple[int, int]) -> Image | None`

- [x] **Step 1: Write the failing test**

Append to `tests/test_digits.py`:

```python
import numpy as np

import digits


def test_crop_is_relative_to_the_anchor() -> None:
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    screen[30:40, 50:70] = 255  # a white bar at (50,30), 20x10

    region = config.Region(dx=10, dy=5, w=20, h=10)
    crop = digits.crop(screen, region, anchor=(40, 25))

    assert crop is not None
    assert crop.shape[:2] == (10, 20)
    assert crop.mean() == 255.0  # we landed exactly on the bar


def test_crop_off_screen_returns_none() -> None:
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    region = config.Region(dx=0, dy=0, w=20, h=10)
    assert digits.crop(screen, region, anchor=(95, 95)) is None


def test_crop_with_negative_offsets() -> None:
    """The anchor is not always above-left of the number it locates."""
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    screen[10:20, 10:30] = 255
    region = config.Region(dx=-30, dy=-30, w=20, h=10)
    crop = digits.crop(screen, region, anchor=(40, 40))
    assert crop is not None
    assert crop.mean() == 255.0
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_digits.py -k crop -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digits'`

- [x] **Step 3: Write the implementation**

Create `digits.py`:

```python
"""Read a number off the screen.

    region  ->  binarise  ->  split into glyphs  ->  match atlas  ->  parse

Every region is anchored to a matched template, never to absolute pixels:
the death modal shifts ~46px vertically depending on whether the
"New Highest Wave!" line is present.

Every public entry point returns None rather than raising. A misread number
must degrade the bot to its phase-2 behaviour, never stop the scan loop.
"""

from __future__ import annotations

import logging

import config
from device import Image

logger = logging.getLogger("tower_bot.digits")


def crop(screen: Image, region: config.Region, anchor: tuple[int, int]) -> Image | None:
    """Cut `region` out of `screen`, measured from `anchor`'s top-left.

    Returns None if the rectangle falls outside the frame - a wrong anchor
    or a resolution change should read as "no number here", not an
    IndexError inside the scan loop.
    """
    x = anchor[0] + region.dx
    y = anchor[1] + region.dy
    height, width = screen.shape[:2]

    if x < 0 or y < 0 or x + region.w > width or y + region.h > height:
        logger.debug(
            "region %s from anchor %s falls outside %dx%d", region, anchor, width, height
        )
        return None

    patch = screen[y : y + region.h, x : x + region.w]
    if patch.size == 0:
        return None
    return patch
```

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_digits.py -k crop -v`
Expected: PASS (3 tests)

- [x] **Step 5: Commit**

```bash
git add digits.py tests/test_digits.py
git commit -m "feat: crop anchor-relative regions for digit reading"
```

---

### Task 3: Segment a region into glyphs

**Files:**
- Modify: `digits.py`
- Test: `tests/test_digits.py`

**Interfaces:**
- Consumes: `digits.crop`, `config.DIGIT_BINARY_THRESHOLD`, `config.GLYPH_MIN_WIDTH`
- Produces: `digits.binarize(region: Image, threshold: int = ...) -> Image`, `digits.glyph_spans(binary: Image, min_width: int = ...) -> list[tuple[int, int]]`, `digits.split_glyphs(binary: Image, min_width: int = ...) -> list[Image]`

- [x] **Step 1: Make `tests/` an importable package**

`render_text` below is the first helper shared across test modules — Tasks 7 and 12 import it. `tests/` has no `__init__.py`, so `from tests.test_digits import render_text` fails with `ModuleNotFoundError`. `conftest.py` already puts the repo root on `sys.path`; the package marker is the missing half.

```bash
touch tests/__init__.py
```

- [x] **Step 2: Write the failing test**

Append to `tests/test_digits.py`:

```python
def render_text(text: str, scale: float = 2.0, thickness: int = 3) -> np.ndarray:
    """White text on black, the same polarity as the game's HUD.

    Synthetic on purpose: it exercises the segmentation algorithm without an
    emulator, which the suite must run without.
    """
    canvas = np.zeros((80, 60 * len(text) + 40, 3), dtype=np.uint8)
    cv2.putText(
        canvas, text, (20, 55), cv2.FONT_HERSHEY_SIMPLEX, scale,
        (255, 255, 255), thickness, cv2.LINE_AA,
    )
    return canvas


def test_binarize_splits_light_glyphs_from_dark_background() -> None:
    binary = digits.binarize(render_text("8"))
    assert set(np.unique(binary)) <= {0, 255}
    assert (binary > 0).any()


def test_segments_each_digit_separately() -> None:
    binary = digits.binarize(render_text("1234"))
    spans = digits.glyph_spans(binary)
    assert len(spans) == 4


def test_segments_keep_left_to_right_order() -> None:
    binary = digits.binarize(render_text("1234"))
    spans = digits.glyph_spans(binary)
    assert spans == sorted(spans)
    assert all(x0 < x1 for x0, x1 in spans)


def test_decimal_point_survives_the_min_width_filter() -> None:
    """The dot is the narrowest real glyph. Filter it out and 1.5K reads 15K."""
    binary = digits.binarize(render_text("1.5K"))
    assert len(digits.glyph_spans(binary)) == 4


def test_split_glyphs_trims_vertically() -> None:
    glyphs = digits.split_glyphs(digits.binarize(render_text("7")))
    assert len(glyphs) == 1
    glyph = glyphs[0]
    assert (glyph[0, :] > 0).any(), "top row should touch the glyph"
    assert (glyph[-1, :] > 0).any(), "bottom row should touch the glyph"


def test_empty_region_yields_no_glyphs() -> None:
    blank = np.zeros((40, 100, 3), dtype=np.uint8)
    assert digits.split_glyphs(digits.binarize(blank)) == []
```

- [x] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_digits.py -k "binarize or segment or glyph or decimal or empty_region" -v`
Expected: FAIL with `AttributeError: module 'digits' has no attribute 'binarize'`

- [x] **Step 4: Write the implementation**

Add to `digits.py` (imports first: `import cv2`, `import numpy as np`):

```python
def binarize(region: Image, threshold: int = config.DIGIT_BINARY_THRESHOLD) -> Image:
    """Light glyphs -> 255, dark panel -> 0."""
    grey = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(grey, threshold, 255, cv2.THRESH_BINARY)
    return binary


def glyph_spans(
    binary: Image, min_width: int = config.GLYPH_MIN_WIDTH
) -> list[tuple[int, int]]:
    """Column ranges [x0, x1) holding a glyph, left to right.

    Column projection rather than contours: the game's font has no
    disconnected glyphs at these sizes, and a projection is trivial to
    reason about when a threshold is slightly off.
    """
    columns = (binary > 0).any(axis=0)
    spans: list[tuple[int, int]] = []
    start: int | None = None

    for x, filled in enumerate(columns):
        if filled and start is None:
            start = x
        elif not filled and start is not None:
            if x - start >= min_width:
                spans.append((start, x))
            start = None

    if start is not None and len(columns) - start >= min_width:
        spans.append((start, len(columns)))
    return spans


def split_glyphs(binary: Image, min_width: int = config.GLYPH_MIN_WIDTH) -> list[Image]:
    """One tightly-cropped binary image per glyph, left to right."""
    glyphs: list[Image] = []
    for x0, x1 in glyph_spans(binary, min_width):
        column = binary[:, x0:x1]
        rows = np.flatnonzero((column > 0).any(axis=1))
        if rows.size == 0:
            continue
        glyphs.append(column[rows[0] : rows[-1] + 1, :])
    return glyphs
```

- [x] **Step 5: Run the tests**

Run: `uv run pytest tests/test_digits.py -v`
Expected: PASS

If `test_decimal_point_survives_the_min_width_filter` fails, `GLYPH_MIN_WIDTH` is too high for the rendered scale — lower it in `config.py` and re-run. Do not "fix" it by asserting 3 glyphs; that is the exact silent misread the test exists to catch.

- [x] **Step 6: Commit**

```bash
git add digits.py tests/test_digits.py tests/__init__.py
git commit -m "feat: segment a region into glyphs by column projection"
```

---

### Task 4: The glyph atlas

**Files:**
- Modify: `digits.py`
- Test: `tests/test_digits.py`

**Interfaces:**
- Consumes: `digits.split_glyphs`, `config.ATLAS_DIR`, `config.GLYPH_MATCH_THRESHOLD`
- Produces: `digits.GLYPH_FILENAMES: dict[str, str]`, `digits.Atlas(directory: Path)` with `.match(glyph: Image, threshold: float = ...) -> str | None` and `.labels -> set[str]`, `digits.AtlasCache(root: Path)` with `.get(size_class: str) -> Atlas | None`

- [x] **Step 1: Write the failing test**

Append to `tests/test_digits.py`:

```python
def build_synthetic_atlas(directory: Path, text: str = "0123456789") -> None:
    """Cut glyphs out of a render and save them under their labels.

    Round-tripping the same renderer proves the matcher, without needing the
    game's font committed to the repo.
    """
    directory.mkdir(parents=True, exist_ok=True)
    glyphs = digits.split_glyphs(digits.binarize(render_text(text)))
    assert len(glyphs) == len(text), f"renderer produced {len(glyphs)} glyphs"
    for char, glyph in zip(text, glyphs):
        cv2.imwrite(str(directory / f"{digits.GLYPH_FILENAMES[char]}.png"), glyph)


def test_atlas_matches_every_digit_it_was_built_from(tmp_path: Path) -> None:
    build_synthetic_atlas(tmp_path)
    atlas = digits.Atlas(tmp_path)

    glyphs = digits.split_glyphs(digits.binarize(render_text("0123456789")))
    assert [atlas.match(g) for g in glyphs] == list("0123456789")


def test_atlas_rejects_a_glyph_below_threshold(tmp_path: Path) -> None:
    build_synthetic_atlas(tmp_path, text="1")
    atlas = digits.Atlas(tmp_path)
    noise = np.random.default_rng(0).integers(0, 2, (30, 20), dtype=np.uint8) * 255
    assert atlas.match(noise, threshold=0.99) is None


def test_atlas_loads_punctuation_by_filename(tmp_path: Path) -> None:
    """'.' and ',' cannot be filenames, so the atlas maps names to chars."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    glyphs = digits.split_glyphs(digits.binarize(render_text("1.5")))
    for name, glyph in zip(["1", "dot", "5"], glyphs):
        cv2.imwrite(str(tmp_path / f"{name}.png"), glyph)
    assert digits.Atlas(tmp_path).labels == {"1", ".", "5"}


def test_missing_atlas_directory_returns_none(tmp_path: Path) -> None:
    assert digits.AtlasCache(tmp_path).get("wallet") is None


def test_atlas_cache_loads_each_size_class_once(tmp_path: Path) -> None:
    build_synthetic_atlas(tmp_path / "wallet")
    cache = digits.AtlasCache(tmp_path)
    assert cache.get("wallet") is cache.get("wallet")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_digits.py -k atlas -v`
Expected: FAIL with `AttributeError: module 'digits' has no attribute 'GLYPH_FILENAMES'`

- [x] **Step 3: Write the implementation**

Add to `digits.py` (add `from pathlib import Path` to the imports):

```python
# A filesystem cannot hold a file called ".png", and "," is awkward in a
# shell, so punctuation glyphs are stored under a name and mapped back here.
GLYPH_FILENAMES: dict[str, str] = {
    **{ch: ch for ch in "0123456789KMBT"},
    ".": "dot",
    ",": "comma",
    "$": "dollar",
}
GLYPH_LABELS: dict[str, str] = {name: char for char, name in GLYPH_FILENAMES.items()}

# The three text sizes the UI renders numbers at. matchTemplate is not
# scale-invariant, so each needs its own atlas.
SIZE_CLASSES: tuple[str, ...] = ("wallet", "price", "modal")


class Atlas:
    """Labelled glyph images for one size class."""

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._glyphs: dict[str, Image] = {}
        for path in sorted(self._dir.glob("*.png")):
            label = GLYPH_LABELS.get(path.stem)
            if label is None:
                logger.warning("ignoring unlabelled atlas file: %s", path.name)
                continue
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                logger.warning("unreadable atlas file: %s", path.name)
                continue
            self._glyphs[label] = image

    @property
    def labels(self) -> set[str]:
        return set(self._glyphs)

    def match(
        self, glyph: Image, threshold: float = config.GLYPH_MATCH_THRESHOLD
    ) -> str | None:
        """Best-scoring label for `glyph`, or None if nothing clears threshold.

        The glyph is resized to each candidate rather than the other way
        round: segmentation crops tightly, so shapes are comparable even when
        the capture is a pixel or two off the atlas entry's size.
        """
        best_label: str | None = None
        best_score = -1.0

        for label, template in self._glyphs.items():
            resized = cv2.resize(
                glyph, (template.shape[1], template.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
            score = float(
                cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED)[0][0]
            )
            if score > best_score:
                best_label, best_score = label, score

        if best_score < threshold:
            return None
        return best_label


class AtlasCache:
    """One Atlas per size class, loaded on first use."""

    def __init__(self, root: Path = config.ATLAS_DIR) -> None:
        self._root = Path(root)
        self._cache: dict[str, Atlas | None] = {}

    def get(self, size_class: str) -> Atlas | None:
        """The atlas for `size_class`, or None if it has not been built.

        None rather than an exception: an unbuilt atlas must degrade the bot
        to brightness affordability, not stop it from running at all.
        """
        if size_class in self._cache:
            return self._cache[size_class]

        directory = self._root / size_class
        atlas: Atlas | None = None
        if directory.is_dir():
            candidate = Atlas(directory)
            if candidate.labels:
                atlas = candidate
            else:
                logger.warning("atlas directory %s has no labelled glyphs", directory)
        else:
            logger.warning("no atlas for size class %r at %s", size_class, directory)

        self._cache[size_class] = atlas
        return atlas
```

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_digits.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add digits.py tests/test_digits.py
git commit -m "feat: add per-size-class glyph atlas with graceful absence"
```

---

### Task 5: Parse glyph labels into a number

**Files:**
- Modify: `digits.py`
- Test: `tests/test_digits.py`

**Interfaces:**
- Consumes: nothing
- Produces: `digits.SUFFIXES: dict[str, int]`, `digits.parse_number(text: str) -> int | None`

- [x] **Step 1: Write the failing test**

Append to `tests/test_digits.py`:

```python
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0),
        ("7", 7),
        ("1234", 1234),
        ("1,234", 1234),
        ("$980", 980),
        ("$1,234,567", 1234567),
        ("1.23K", 1230),
        ("4.5M", 4500000),
        ("2B", 2000000000),
        ("1.5T", 1500000000000),
        ("$12.75K", 12750),
    ],
)
def test_parses_the_games_number_formats(text: str, expected: int) -> None:
    assert digits.parse_number(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "$", "K", "..", "12..3", "1.2X", "abc"])
def test_unparseable_text_returns_none(text: str) -> None:
    assert digits.parse_number(text) is None


def test_suffix_is_not_silently_dropped() -> None:
    """A digit-only parser reads 1.23K as 123 and looks fine for an hour."""
    assert digits.parse_number("1.23K") != 123
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_digits.py -k "parse or suffix or unparseable" -v`
Expected: FAIL with `AttributeError: module 'digits' has no attribute 'parse_number'`

- [x] **Step 3: Write the implementation**

Add to `digits.py` (add `import re` to the imports):

```python
# The game abbreviates large numbers. A long-running bot hits these within
# an hour or two, and a digit-only parser misreads them silently.
SUFFIXES: dict[str, int] = {
    "K": 1_000,
    "M": 1_000_000,
    "B": 1_000_000_000,
    "T": 1_000_000_000_000,
}

_NUMBER = re.compile(r"\d+(\.\d+)?$")


def parse_number(text: str) -> int | None:
    """`$1.23K` -> 1230. None if the text is not a number the game renders."""
    cleaned = text.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None

    multiplier = 1
    if cleaned[-1] in SUFFIXES:
        multiplier = SUFFIXES[cleaned[-1]]
        cleaned = cleaned[:-1]

    if not _NUMBER.match(cleaned):
        return None
    return int(round(float(cleaned) * multiplier))
```

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_digits.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add digits.py tests/test_digits.py
git commit -m "feat: parse abbreviated game numbers with K/M/B/T suffixes"
```

---

### Task 6: NumberReader — region to integer

**Files:**
- Modify: `digits.py`
- Test: `tests/test_digits.py`

**Interfaces:**
- Consumes: `digits.crop`, `digits.split_glyphs`, `digits.AtlasCache`, `digits.parse_number`
- Produces: `digits.NumberReader(atlases: AtlasCache)` with `.read(screen: Image, region: config.Region, anchor: tuple[int, int], size_class: str) -> int | None`

- [x] **Step 1: Write the failing test**

Append to `tests/test_digits.py`:

```python
def paste(screen: np.ndarray, patch: np.ndarray, at: tuple[int, int]) -> None:
    x, y = at
    screen[y : y + patch.shape[0], x : x + patch.shape[1]] = patch


def test_reads_a_number_off_a_synthetic_screen(tmp_path: Path) -> None:
    build_synthetic_atlas(tmp_path / "wallet")
    reader = digits.NumberReader(digits.AtlasCache(tmp_path))

    rendered = render_text("4271")
    screen = np.zeros((400, 400, 3), dtype=np.uint8)
    paste(screen, rendered, (100, 100))

    region = config.Region(dx=100, dy=100, w=rendered.shape[1], h=rendered.shape[0])
    assert reader.read(screen, region, anchor=(0, 0), size_class="wallet") == 4271


def test_read_without_an_atlas_returns_none(tmp_path: Path) -> None:
    reader = digits.NumberReader(digits.AtlasCache(tmp_path))
    screen = np.zeros((400, 400, 3), dtype=np.uint8)
    region = config.Region(dx=0, dy=0, w=100, h=40)
    assert reader.read(screen, region, anchor=(0, 0), size_class="wallet") is None


def test_read_off_screen_returns_none(tmp_path: Path) -> None:
    build_synthetic_atlas(tmp_path / "wallet")
    reader = digits.NumberReader(digits.AtlasCache(tmp_path))
    screen = np.zeros((50, 50, 3), dtype=np.uint8)
    region = config.Region(dx=0, dy=0, w=100, h=40)
    assert reader.read(screen, region, anchor=(0, 0), size_class="wallet") is None


def test_one_unrecognised_glyph_fails_the_whole_read(tmp_path: Path) -> None:
    """A partial read is worse than none: 1?34 must not become 134.

    That is the failure that would let the bot buy an upgrade it cannot
    afford while reporting a plausible price.
    """
    build_synthetic_atlas(tmp_path / "wallet", text="0123456789")
    reader = digits.NumberReader(digits.AtlasCache(tmp_path))

    rendered = render_text("12")
    # Overwrite the second glyph with a solid block no digit matches.
    rendered[10:70, 80:130] = 255
    screen = np.zeros((400, 400, 3), dtype=np.uint8)
    paste(screen, rendered, (0, 0))

    region = config.Region(dx=0, dy=0, w=rendered.shape[1], h=rendered.shape[0])
    assert reader.read(
        screen, region, anchor=(0, 0), size_class="wallet"
    ) is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_digits.py -k "reads_a_number or read_without or read_off_screen or unrecognised" -v`
Expected: FAIL with `AttributeError: module 'digits' has no attribute 'NumberReader'`

- [x] **Step 3: Write the implementation**

Add to `digits.py`:

```python
class NumberReader:
    """Turns a screen region into an integer, or None."""

    def __init__(self, atlases: AtlasCache | None = None) -> None:
        self._atlases = atlases if atlases is not None else AtlasCache()

    def read(
        self,
        screen: Image,
        region: config.Region,
        anchor: tuple[int, int],
        size_class: str,
    ) -> int | None:
        """Read the number in `region`, measured from `anchor`.

        Any failure along the way - no atlas, region off-screen, a glyph
        that matches nothing - returns None. A partially recognised number
        is NOT returned: reading "1?34" as 134 would let the bot act on a
        price that looks plausible and is wrong by an order of magnitude.
        """
        atlas = self._atlases.get(size_class)
        if atlas is None:
            return None

        patch = crop(screen, region, anchor)
        if patch is None:
            return None

        glyphs = split_glyphs(binarize(patch))
        if not glyphs:
            return None

        labels: list[str] = []
        for glyph in glyphs:
            label = atlas.match(glyph)
            if label is None:
                logger.debug("unrecognised glyph in %s region", size_class)
                return None
            labels.append(label)

        return parse_number("".join(labels))
```

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_digits.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add digits.py tests/test_digits.py
git commit -m "feat: add NumberReader turning a screen region into an integer"
```

---

### Task 7: Bootstrap the atlas from real frames

**Files:**
- Create: `build_atlas.py`
- Test: `tests/test_build_atlas.py`

**Interfaces:**
- Consumes: `digits.split_glyphs`, `digits.binarize`, `digits.crop`, `config` regions
- Produces: `build_atlas.dump_glyphs(screen: Image, region: config.Region, anchor: tuple[int, int], out_dir: Path) -> list[Path]`, CLI `uv run build_atlas.py`

- [x] **Step 1: Write the failing test**

Create `tests/test_build_atlas.py`:

```python
"""The atlas bootstrap tool. Labelling is manual; segmentation is not."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

import build_atlas
import config
from tests.test_digits import render_text  # needs tests/__init__.py (Task 3)


def test_dumps_one_file_per_glyph(tmp_path: Path) -> None:
    rendered = render_text("407")
    screen = np.zeros((200, 400, 3), dtype=np.uint8)
    screen[0 : rendered.shape[0], 0 : rendered.shape[1]] = rendered
    region = config.Region(dx=0, dy=0, w=rendered.shape[1], h=rendered.shape[0])

    written = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)

    assert len(written) == 3
    assert all(p.exists() for p in written)
    assert sorted(p.name for p in written) == [
        "glyph_000.png", "glyph_001.png", "glyph_002.png"
    ]


def test_dumped_glyphs_are_readable_greyscale(tmp_path: Path) -> None:
    rendered = render_text("5")
    screen = np.zeros((200, 400, 3), dtype=np.uint8)
    screen[0 : rendered.shape[0], 0 : rendered.shape[1]] = rendered
    region = config.Region(dx=0, dy=0, w=rendered.shape[1], h=rendered.shape[0])

    written = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)
    img = cv2.imread(str(written[0]), cv2.IMREAD_GRAYSCALE)
    assert img is not None and img.size > 0


def test_does_not_overwrite_existing_numbering(tmp_path: Path) -> None:
    """Called twice over a session, the second batch must not clobber the first."""
    rendered = render_text("12")
    screen = np.zeros((200, 400, 3), dtype=np.uint8)
    screen[0 : rendered.shape[0], 0 : rendered.shape[1]] = rendered
    region = config.Region(dx=0, dy=0, w=rendered.shape[1], h=rendered.shape[0])

    build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)
    second = build_atlas.dump_glyphs(screen, region, (0, 0), tmp_path)

    assert len(list(tmp_path.glob("glyph_*.png"))) == 4
    assert second[0].name == "glyph_002.png"
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_atlas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'build_atlas'`

- [x] **Step 3: Write the implementation**

Create `build_atlas.py`:

```python
"""Bootstrap the glyph atlas.

Captures frames from the emulator, cuts the configured number regions out of
them, segments those into glyphs, and writes each glyph as an unlabelled
file. You then LABEL them once, by renaming:

    templates/atlas/wallet/glyph_003.png  ->  templates/atlas/wallet/7.png

Punctuation uses names, because a file cannot be called ".png":

    .  ->  dot.png      ,  ->  comma.png      $  ->  dollar.png

Expect 10-30 files per size class before every glyph has been seen. Run it
across a session so large numbers (and their K/M/B suffixes) show up.

    uv run build_atlas.py --size-class wallet --frames 20
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

import config
import digits
import screens
import vision
from device import Image, capture_screen, connect_device

# Which anchor each size class's region is measured from.
REGION_SOURCES: dict[str, tuple[config.Region, str]] = {
    "wallet": (config.WALLET_REGION, "IN_RUN"),
    "modal": (config.MODAL_COINS_REGION, "GAME_OVER"),
}


def dump_glyphs(
    screen: Image, region: config.Region, anchor: tuple[int, int], out_dir: Path
) -> list[Path]:
    """Write one PNG per glyph found in `region`. Returns the paths written.

    Numbering continues from whatever is already in `out_dir`, so repeated
    calls across a session accumulate instead of overwriting.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    start = len(list(out_dir.glob("glyph_*.png")))

    patch = digits.crop(screen, region, anchor)
    if patch is None:
        return []

    written: list[Path] = []
    for offset, glyph in enumerate(digits.split_glyphs(digits.binarize(patch))):
        path = out_dir / f"glyph_{start + offset:03d}.png"
        cv2.imwrite(str(path), glyph)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dump unlabelled glyphs for labelling.")
    parser.add_argument("--size-class", choices=sorted(REGION_SOURCES), required=True)
    parser.add_argument("--frames", type=int, default=20, help="frames to sample")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between frames")
    parser.add_argument("--host", default=config.DEVICE_HOST)
    parser.add_argument("--port", type=int, default=config.DEVICE_PORT)
    args = parser.parse_args(argv)

    region, anchor_name = REGION_SOURCES[args.size_class]
    if region.w == 0 or region.h == 0:
        raise SystemExit(
            f"the {args.size_class} region has not been calibrated yet - "
            "measure it with tools/crop_preview.py and write it into config.py first"
        )

    out_dir = config.ATLAS_DIR / args.size_class
    device = connect_device(args.host, args.port)
    cache = vision.TemplateCache(config.TEMPLATE_DIR)

    total = 0
    for frame in range(args.frames):
        screen = capture_screen(device)
        reading = screens.classify(screen, cache)
        if reading.state.value != anchor_name or reading.top_left is None:
            print(f"frame {frame}: on {reading.state.value}, need {anchor_name} - skipping")
            time.sleep(args.interval)
            continue

        written = dump_glyphs(screen, region, reading.top_left, out_dir)
        total += len(written)
        print(f"frame {frame}: +{len(written)} glyphs ({total} total)")
        time.sleep(args.interval)

    print(f"\n{total} glyphs in {out_dir}")
    print("Now label them by renaming, then delete any duplicates and blanks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_build_atlas.py -v`
Expected: PASS (3 tests)

- [x] **Step 5: Commit**

```bash
git add build_atlas.py tests/test_build_atlas.py
git commit -m "feat: add atlas bootstrap tool for one-time glyph labelling"
```

---

### Task 8: Label the real atlas and prove it against a fixture

The one irreducibly manual task. It needs the emulator and about twenty minutes.

> **Revised after calibration.** The death modal does not render bare numbers.
> Its lines read `Wave 1`, `Tier 1` and `0 ©`, and they are **centred**, so the
> digits slide left as they grow — a crop tight to today's `1` misses tomorrow's
> `137`. The regions therefore span the whole line, and the `modal` atlas must
> include the caption glyphs `W a v e T i r` and the coin icon `©` alongside the
> digits. `parse_number` extracts the single numeric run and allows known
> captions around it. `wallet` and `price` are unaffected — those are bare
> numbers.

**Files:**
- Create: `templates/atlas/wallet/*.png`, `templates/atlas/price/*.png`, `templates/atlas/modal/*.png`
- Test: `tests/test_atlas_real.py`

**Interfaces:**
- Consumes: `build_atlas.dump_glyphs`, `digits.NumberReader`
- Produces: a committed atlas that reads the Task 1 fixtures correctly

- [ ] **Step 1: Collect glyphs across a live session**

```bash
uv run build_atlas.py --size-class wallet --frames 30
uv run build_atlas.py --size-class modal --frames 10   # during death modals
```

The `price` class has no live collector (prices sit beside a matched label, not an anchor); cut those by hand from `tests/fixtures/in_run_wallet.png` with `tools/crop_preview.py` and segment them with a one-off `python -c` using `digits.split_glyphs`.

- [ ] **Step 2: Label by renaming**

For each size class, open the dumped files and rename to the glyph shown:

```bash
cd templates/atlas/wallet
mv glyph_003.png 7.png     # and so on
mv glyph_011.png dot.png
mv glyph_014.png K.png
```

Delete duplicates and blanks. Every size class must end with at least `0`–`9`; `dot`, `comma`, `dollar`, `K`, `M`, `B`, `T` as they appear. The `modal` class additionally needs the caption glyphs — `cap_w`, `cap_a`, `cap_v`, `cap_e`, `cap_i`, `cap_r` and `coin` — or every modal line reads as `None`. (Lowercase letters get spelled-out filenames because macOS filesystems are case-insensitive: `t.png` and `T.png` would be the same file.) A missing glyph makes every number containing it read as `None` — safe, but the number is simply never available.

> **Steps 1-2 still open.** They are done for `wallet` (0-9 plus `$`) and only
> partly for `price` (0,1,2,4,5) and `modal` (0,1,2,6 plus the caption glyphs).
> The remaining digits need a live session: deeper runs and higher tiers are
> what put them on screen. `tests/test_atlas_real.py::test_size_class_has_a_full_digit_set`
> is `xfail(strict)` for the two incomplete classes, so it turns into a failure
> the moment a class is finished and the marker goes stale.

- [x] **Step 3: Write the test that proves the atlas works**

Create `tests/test_atlas_real.py`. Replace the expected values with the ground truth you wrote down in Task 1 Step 3:

```python
"""The committed atlas against the committed frames. This is the real proof.

Everything in test_digits.py round-trips a synthetic renderer, which proves
the algorithm and nothing about the game's font. This file is what catches a
mislabelled glyph.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

import config
import digits
import screens
import vision

FIXTURES = Path(__file__).parent / "fixtures"

# Ground truth: what a human read off these frames with their own eyes.
EXPECTED_WALLET = 0     # <- from Task 1 Step 3
EXPECTED_WAVE = 0       # <- from Task 1 Step 3
EXPECTED_COINS = 0      # <- from Task 1 Step 3
EXPECTED_TIER = 0       # <- from Task 1 Step 3


def read(fixture: str, region: config.Region, anchor_name: str, size_class: str) -> int | None:
    screen = cv2.imread(str(FIXTURES / fixture), cv2.IMREAD_COLOR)
    assert screen is not None, f"missing fixture: {fixture}"
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    reading = screens.classify(screen, cache)
    assert reading.state.value == anchor_name, f"{fixture} reads as {reading.state.value}"
    assert reading.top_left is not None
    reader = digits.NumberReader()
    return reader.read(screen, region, reading.top_left, size_class)


def test_reads_the_wallet() -> None:
    assert read("in_run_wallet.png", config.WALLET_REGION, "IN_RUN", "wallet") == EXPECTED_WALLET


@pytest.mark.parametrize(
    ("region_name", "expected"),
    [
        ("MODAL_WAVE_REGION", EXPECTED_WAVE),
        ("MODAL_COINS_REGION", EXPECTED_COINS),
        ("MODAL_TIER_REGION", EXPECTED_TIER),
    ],
)
def test_reads_the_death_modal(region_name: str, expected: int) -> None:
    region = getattr(config, region_name)
    assert read("game_over_stats.png", region, "GAME_OVER", "modal") == expected


def test_every_size_class_has_a_full_digit_set() -> None:
    """A missing digit is silent: numbers containing it just read as None."""
    cache = digits.AtlasCache()
    for size_class in digits.SIZE_CLASSES:
        atlas = cache.get(size_class)
        assert atlas is not None, f"no atlas for {size_class}"
        missing = set("0123456789") - atlas.labels
        assert not missing, f"{size_class} atlas is missing {sorted(missing)}"
```

- [x] **Step 4: Run the test**

Run: `uv run pytest tests/test_atlas_real.py -v`
Expected: PASS

A wrong digit means a mislabelled file — find it by dumping the intermediate labels:

```bash
uv run python -c "
import cv2, config, digits, screens, vision
s = cv2.imread('tests/fixtures/in_run_wallet.png')
r = screens.classify(s, vision.TemplateCache(config.TEMPLATE_DIR))
patch = digits.crop(s, config.WALLET_REGION, r.top_left)
atlas = digits.AtlasCache().get('wallet')
print([atlas.match(g) for g in digits.split_glyphs(digits.binarize(patch))])
"
```

- [x] **Step 5: Commit**

```bash
git add templates/atlas tests/test_atlas_real.py
git commit -m "feat: commit the labelled glyph atlas and its fixture proof"
```

---

### Task 9: DigitAffordability

**Files:**
- Modify: `affordability.py`
- Test: `tests/test_digit_affordability.py`

**Interfaces:**
- Consumes: `digits.NumberReader`, `config.PRICE_REGION`, `vision.Match`
- Produces: `affordability.DigitAffordability(reader, fallback)` with `.wallet: int | None`, `.last_price: int | None`, `.last_wallet: int | None`; `AffordabilityCheck` protocol gains `last_price` / `last_wallet`

- [x] **Step 1: Write the failing test**

Create `tests/test_digit_affordability.py`:

```python
"""Exact affordability, and what it does when a read fails."""

from __future__ import annotations

import numpy as np
import pytest

import config
import vision
from affordability import BrightnessAffordability, DigitAffordability


class FakeReader:
    """Returns a scripted price and records what it was asked for."""

    def __init__(self, price: int | None) -> None:
        self.price = price
        self.calls: list[tuple[config.Region, tuple[int, int], str]] = []

    def read(self, screen, region, anchor, size_class):  # type: ignore[no-untyped-def]
        self.calls.append((region, anchor, size_class))
        return self.price


@pytest.fixture()
def scene() -> tuple[np.ndarray, vision.Match, np.ndarray, config.Action]:
    screen = np.full((200, 200, 3), 200, dtype=np.uint8)
    template = np.full((10, 10, 3), 200, dtype=np.uint8)
    match = vision.Match(center=(55, 55), score=1.0, top_left=(50, 50))
    action = config.Action(name="Damage", template="upgrade_damage.png")
    return screen, match, template, action


def test_affordable_when_wallet_covers_the_price(scene) -> None:
    screen, match, template, action = scene
    check = DigitAffordability(FakeReader(price=100), BrightnessAffordability())
    check.wallet = 250

    ok, detail = check.affordable(screen, match, template, action)

    assert ok is True
    assert check.last_price == 100
    assert check.last_wallet == 250


def test_rejected_when_wallet_is_short(scene) -> None:
    screen, match, template, action = scene
    check = DigitAffordability(FakeReader(price=500), BrightnessAffordability())
    check.wallet = 250

    ok, detail = check.affordable(screen, match, template, action)

    assert ok is False
    assert "250" in detail and "500" in detail


def test_exactly_enough_is_affordable(scene) -> None:
    screen, match, template, action = scene
    check = DigitAffordability(FakeReader(price=250), BrightnessAffordability())
    check.wallet = 250
    ok, _ = check.affordable(screen, match, template, action)
    assert ok is True


def test_price_is_read_relative_to_the_matched_label(scene) -> None:
    """Not from a screen anchor: each upgrade has its own price box."""
    screen, match, template, action = scene
    reader = FakeReader(price=10)
    check = DigitAffordability(reader, BrightnessAffordability())
    check.wallet = 100

    check.affordable(screen, match, template, action)

    region, anchor, size_class = reader.calls[0]
    assert region == config.PRICE_REGION
    assert anchor == match.top_left
    assert size_class == "price"


def test_unreadable_price_falls_back_to_brightness(scene) -> None:
    screen, match, template, action = scene
    check = DigitAffordability(FakeReader(price=None), BrightnessAffordability())
    check.wallet = 250

    ok, detail = check.affordable(screen, match, template, action)

    assert ok is True  # brightness ratio is 1.0 on this uniform scene
    assert check.last_price is None


def test_unknown_wallet_falls_back_to_brightness(scene) -> None:
    screen, match, template, action = scene
    dim = np.full((200, 200, 3), 40, dtype=np.uint8)
    check = DigitAffordability(FakeReader(price=10), BrightnessAffordability())
    check.wallet = None

    ok, detail = check.affordable(dim, match, template, action)

    assert ok is False  # the brightness gate still catches the dimmed modal
    assert "brightness" in detail


def test_brightness_check_reports_no_price() -> None:
    """The protocol's price fields exist on both implementations."""
    check = BrightnessAffordability()
    assert check.last_price is None
    assert check.last_wallet is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_digit_affordability.py -v`
Expected: FAIL with `ImportError: cannot import name 'DigitAffordability'`

- [x] **Step 3: Write the implementation**

In `affordability.py`, add `last_price` / `last_wallet` to the protocol and to `BrightnessAffordability`, then add the new class:

```python
class AffordabilityCheck(Protocol):
    # What the last affordable() call learned about the numbers, for the
    # Tapped event. Brightness never learns anything and leaves both None.
    last_price: int | None
    last_wallet: int | None

    def affordable(
        self,
        screen: Image,
        match: vision.Match,
        template: Image,
        action: config.Action,
    ) -> tuple[bool, str]:
        ...
```

Add to `BrightnessAffordability` as class attributes:

```python
class BrightnessAffordability:
    last_price: int | None = None
    last_wallet: int | None = None
```

Then append:

```python
class DigitAffordability:
    """Compare the wallet against the price. Exact, where brightness guesses.

    The wallet is read once per scan by the bot and pushed in, rather than
    re-read per action: it is the same number for all four upgrades, and the
    protocol hands us only the matched upgrade label, not the IN_RUN anchor
    the wallet region is measured from.

    Every failure degrades to `fallback` rather than blocking. Spec section
    12: if segmentation proves unreliable, brightness affordability stays and
    only the dashboard's numbers are lost.
    """

    def __init__(
        self,
        reader: digits.NumberReader,
        fallback: AffordabilityCheck | None = None,
    ) -> None:
        self._reader = reader
        self._fallback: AffordabilityCheck = fallback or BrightnessAffordability()
        self.wallet: int | None = None
        self.last_price: int | None = None
        self.last_wallet: int | None = None

    def affordable(
        self,
        screen: Image,
        match: vision.Match,
        template: Image,
        action: config.Action,
    ) -> tuple[bool, str]:
        price = self._reader.read(screen, config.PRICE_REGION, match.top_left, "price")
        self.last_price = price
        self.last_wallet = self.wallet

        if price is None or self.wallet is None:
            return self._fallback.affordable(screen, match, template, action)

        if self.wallet < price:
            return False, f"wallet {self.wallet} < price {price}"
        return True, ""
```

Add `import digits` to the imports.

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_digit_affordability.py -v`
Expected: PASS (7 tests)

- [x] **Step 5: Commit**

```bash
git add affordability.py tests/test_digit_affordability.py
git commit -m "feat: add exact digit affordability with brightness fallback"
```

---

### Task 10: Report wallet and price on the events

**Files:**
- Modify: `tower_bot.py:47-130` (`TowerBot.__init__`, `find_and_click_image`), `tower_bot.py:134-205` (`run_once`)
- Test: `tests/test_bot_reporting.py`

**Interfaces:**
- Consumes: `digits.NumberReader`, `affordability.DigitAffordability`, `screens.ScreenReading.top_left`
- Produces: `TowerBot.wallet: int | None`; `Tapped.price` / `Tapped.wallet` / `ScanCompleted.wallet` populated; `Skipped.reason == "unaffordable"`

- [x] **Step 1: Write the failing test**

Append to `tests/test_bot_reporting.py`. It already has the harness — `make_bot(fixture)` returns `(bot, recorder, device)` with a frame injected and no emulator, `Recorder.of(kind)` filters the published events, and `frame(name)` loads a fixture. Add one scripted reader beside them:

```python
class ScriptedReader:
    """Returns a fixed number per size class, and records the regions asked for.

    Stands in for digits.NumberReader so these tests exercise the bot's
    wiring, not the segmenter - that is test_digits.py's job.
    """

    def __init__(self, **by_region: int | None) -> None:
        self._by_region = by_region
        self.calls: list[tuple[str, tuple[int, int]]] = []

    def read(self, screen, region, anchor, size_class):  # type: ignore[no-untyped-def]
        self.calls.append((size_class, anchor))
        if region == config.WALLET_REGION:
            return self._by_region.get("wallet")
        if region == config.PRICE_REGION:
            return self._by_region.get("price")
        if region == config.MODAL_WAVE_REGION:
            return self._by_region.get("wave")
        if region == config.MODAL_COINS_REGION:
            return self._by_region.get("coins")
        if region == config.MODAL_TIER_REGION:
            return self._by_region.get("tier")
        return None


def make_digit_bot(
    fixture: str, **numbers: int | None
) -> tuple[TowerBot, Recorder, MagicMock]:
    """make_bot, but reading numbers instead of guessing at brightness."""
    dev = MagicMock()
    bus = events.EventBus()
    rec = Recorder()
    bus.subscribe(rec)
    reader = ScriptedReader(**numbers)
    bot = TowerBot(
        device=dev,
        templates=vision.TemplateCache(TEMPLATES),
        bus=bus,
        reader=reader,
        affordability_check=DigitAffordability(reader, BrightnessAffordability()),
    )
    bot._screen = frame(fixture)
    return bot, rec, dev


def settle(bot: TowerBot, monkeypatch: pytest.MonkeyPatch, scans: int = 2) -> None:
    """Run enough scans for the tracker to confirm the injected screen."""
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    for _ in range(scans):
        bot.run_once()


def test_scan_reports_the_wallet_when_in_run(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, _ = make_digit_bot("in_run_lit", wallet=1250)
    settle(bot, monkeypatch)
    assert rec.of(events.ScanCompleted)[-1].wallet == 1250


def test_scan_reports_no_wallet_outside_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wallet region is measured from the IN_RUN anchor. Off that screen
    there is no origin to measure from, and a stale number is worse than none."""
    bot, rec, _ = make_digit_bot("main_menu", wallet=1250)
    settle(bot, monkeypatch)
    assert rec.of(events.ScanCompleted)[-1].wallet is None


def test_tapped_carries_the_price_and_wallet(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, _ = make_digit_bot("in_run_lit", wallet=1250, price=300)
    settle(bot, monkeypatch)
    tapped = rec.of(events.Tapped)
    assert tapped, "no upgrade was tapped on a lit in-run frame"
    assert tapped[-1].price == 300
    assert tapped[-1].wallet == 1250


def test_unaffordable_upgrade_is_skipped_with_that_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot, rec, dev = make_digit_bot("in_run_lit", wallet=100, price=300)
    settle(bot, monkeypatch)

    reasons = {e.reason for e in rec.of(events.Skipped)}
    assert "unaffordable" in reasons
    assert rec.of(events.Tapped) == []
    dev.shell.assert_not_called()


def test_dimmed_reports_dimmed_not_unaffordable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two reasons are distinct in the spec and must stay distinguishable:
    'dimmed' means we could not tell, 'unaffordable' means we read the numbers.

    With no price available the check falls through to brightness, and the
    game_over fixture is dimmed behind the modal.
    """
    bot, rec, _ = make_digit_bot("in_run_lit", wallet=1250, price=None)
    bot.tracker.state = screens.ScreenState.IN_RUN
    bot.tracker._confirmed = True
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    bot._screen = frame("game_over")  # upgrade labels present but dimmed
    bot.run_once()

    reasons = {e.reason for e in rec.of(events.Skipped)}
    assert "dimmed" in reasons
    assert "unaffordable" not in reasons
```

Add `import config` and `from affordability import BrightnessAffordability, DigitAffordability` to the file's imports.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bot_reporting.py -k "wallet or price or unaffordable or dimmed" -v`
Expected: FAIL — `ScanCompleted.wallet` is `None` and no `unaffordable` reason is emitted.

> **Amended.** The wallet gate below tests only the tracker's state. The
> tracker is debounced, so mid-fade it still says IN_RUN while the frame is
> already the death modal - and `reading.top_left` is then the GAME_OVER
> anchor, which puts the wallet region somewhere else entirely. The shipped
> condition requires `reading.state` to be IN_RUN as well, so the anchor and
> the region always come from the same frame.

- [x] **Step 3: Wire the reader into the bot**

First extend the imports at the top of `tower_bot.py` — it currently imports
only `AffordabilityCheck` and `BrightnessAffordability` from `affordability`,
and does not import `digits` at all:

```python
import digits
from affordability import (
    AffordabilityCheck,
    BrightnessAffordability,
    DigitAffordability,
)
```

In `TowerBot.__init__`, accept the reader and keep the wallet:

```python
        self.reader = reader if reader is not None else digits.NumberReader()
        self.wallet: int | None = None
```

with the new parameter `reader: digits.NumberReader | None = None` in the signature.

In `run_once`, after `state = self.tracker.state` and before the action loop, read the wallet:

```python
        # The wallet region is anchored to the IN_RUN template, so it can only
        # be read on that screen. Clear it elsewhere: a stale wallet would let
        # the affordability gate approve a purchase using last run's cash.
        self.wallet = None
        if state is screens.ScreenState.IN_RUN and reading.top_left is not None:
            self.wallet = self.reader.read(
                self.screen, config.WALLET_REGION, reading.top_left, "wallet"
            )
        if isinstance(self.affordability, DigitAffordability):
            self.affordability.wallet = self.wallet
```

And pass it to the scan event:

```python
        self.bus.publish(
            events.ScanCompleted(
                screen=state.value,
                duration_ms=(time.monotonic() - started) * 1000,
                wallet=self.wallet,
            )
        )
```

- [x] **Step 4: Distinguish the two skip reasons in `find_and_click_image`**

Replace the affordability block:

```python
        ok, detail = self.affordability.affordable(
            self.screen, match, template, action
        )
        if not ok:
            # "unaffordable" means we read both numbers and the wallet was
            # short. "dimmed" means we could not tell and fell back to the
            # brightness heuristic. Collapsing them would hide exactly the
            # regression phase 3 exists to fix.
            reason = (
                "unaffordable"
                if self.affordability.last_price is not None
                else "dimmed"
            )
            self.bus.publish(
                events.Skipped(action=key, reason=reason, detail=detail)
            )
            return False
```

and enrich the tap:

```python
        self.bus.publish(
            events.Tapped(
                action=key,
                x=x,
                y=y,
                score=match.score,
                price=self.affordability.last_price,
                wallet=self.affordability.last_wallet,
            )
        )
```

- [x] **Step 5: Run the tests**

Run: `uv run pytest -q`
Expected: PASS, all suites.

- [x] **Step 6: Commit**

```bash
git add tower_bot.py tests/test_bot_reporting.py
git commit -m "feat: report wallet and price on scan, tap and skip events"
```

---

### Task 11: Read wave, coins and tier off the death modal

**Files:**
- Modify: `events.py` (`RunEnded`), `tower_bot.py:145-160` (the transition block)
- Test: `tests/test_runs.py`, `tests/test_bot_reporting.py`

**Interfaces:**
- Consumes: `digits.NumberReader`, `config.MODAL_*_REGION`, `runs.RunTracker.transition`
- Produces: `events.RunEnded.tier: int | None`; `RunEnded` enriched with wave/coins/tier on the GAME_OVER transition

- [x] **Step 1: Write the failing test**

Append to `tests/test_bot_reporting.py`:

```python
def die(
    bot: TowerBot, monkeypatch: pytest.MonkeyPatch, ending_fixture: str
) -> None:
    """Confirm IN_RUN, then confirm `ending_fixture`, closing the run."""
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    bot._screen = frame("in_run_lit")
    for _ in range(2):
        bot.run_once()
    bot._screen = frame(ending_fixture)
    for _ in range(2):
        bot.run_once()


def test_run_ended_carries_the_modal_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """The frame that CONFIRMED game over is the one we read. By then the
    modal has survived two consecutive readings, so the fade is finished."""
    bot, rec, _ = make_digit_bot("in_run_lit", wave=137, coins=8400, tier=4)
    die(bot, monkeypatch, "game_over")

    ended = rec.of(events.RunEnded)
    assert ended, "the run never closed"
    assert (ended[-1].wave, ended[-1].coins, ended[-1].tier) == (137, 8400, 4)


def test_unreadable_modal_leaves_the_fields_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot, rec, _ = make_digit_bot("in_run_lit")  # reader returns None for all
    die(bot, monkeypatch, "game_over")

    ended = rec.of(events.RunEnded)[-1]
    assert (ended.wave, ended.coins, ended.tier) == (None, None, None)
    assert ended.run_id == 1  # the run still ends


def test_abandoned_run_reads_no_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """IN_RUN -> MAIN_MENU has no modal to read. Do not invent numbers."""
    bot, rec, _ = make_digit_bot("in_run_lit", wave=137, coins=8400, tier=4)
    die(bot, monkeypatch, "main_menu")

    ended = rec.of(events.RunEnded)[-1]
    assert ended.abandoned is True
    assert (ended.wave, ended.coins, ended.tier) == (None, None, None)
```

Append to `tests/test_runs.py`:

```python
def test_run_ended_defaults_tier_to_none() -> None:
    tracker = RunTracker()
    tracker.transition(ScreenState.IN_RUN, now=0.0)
    ended = tracker.transition(ScreenState.GAME_OVER, now=10.0)
    assert ended.tier is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runs.py -k tier -v`
Expected: FAIL with `AttributeError: 'RunEnded' object has no attribute 'tier'`

- [x] **Step 3: Add `tier` to the event**

In `events.py`:

```python
@dataclass(frozen=True, kw_only=True)
class RunEnded(Event):
    run_id: int
    duration: float
    wave: int | None = None
    coins: int | None = None
    tier: int | None = None
    abandoned: bool = False
```

> **Superseded in part.** The `_read_modal_stats` body below anchors all three
> numbers on the matched `game_over` template. That holds for wave only. Tier
> and coins are located by their own caption - see `config.MODAL_TIER_CAPTION` /
> `MODAL_COINS_CAPTION` and `digits.NumberReader.read_at_caption` - because a
> record run's "New Highest Wave!" line pushes them down 49px while the modal's
> top edge rises as it re-centres. Use `read_at_caption` for those two.

- [x] **Step 4: Enrich the event in the bot**

`RunTracker` stays screen-free — it brackets runs and knows nothing about vision. The bot fills the numbers in. In `run_once`, replace the publish of `run_event`:

```python
            run_event = self.runs.transition(self.tracker.state, time.monotonic())
            if run_event is not None:
                if (
                    isinstance(run_event, events.RunEnded)
                    and self.tracker.state is screens.ScreenState.GAME_OVER
                    and reading.top_left is not None
                ):
                    run_event = self._read_modal_stats(run_event, reading.top_left)
                self.bus.publish(run_event)
```

and add the method to `TowerBot`:

```python
    def _read_modal_stats(
        self, ended: events.RunEnded, anchor: tuple[int, int]
    ) -> events.RunEnded:
        """Fill wave / coins / tier from the death modal.

        Only ever called on a CONFIRMED game over, so the modal has already
        survived two consecutive readings and its fade has finished - the
        same debounce that stops phantom run boundaries also guarantees the
        numbers are fully drawn.

        Anchored to the matched game_over template, never absolute: the modal
        shifts ~46px when the "New Highest Wave!" line is present.
        """
        return dataclasses.replace(
            ended,
            wave=self.reader.read(self.screen, config.MODAL_WAVE_REGION, anchor, "modal"),
            coins=self.reader.read(self.screen, config.MODAL_COINS_REGION, anchor, "modal"),
            tier=self.reader.read(self.screen, config.MODAL_TIER_REGION, anchor, "modal"),
        )
```

Add `import dataclasses` to `tower_bot.py`.

- [x] **Step 5: Run the tests**

Run: `uv run pytest -q`
Expected: PASS

- [x] **Step 6: Show the numbers in the sinks**

`sinks/log.py` and `sinks/tui.py` already format `RunEnded`; extend both to include wave, coins and tier when present, following the formatting already in each file. Verify with:

Run: `uv run pytest tests/test_log_sink.py tests/test_tui_sink.py -v`
Expected: PASS

- [x] **Step 7: Commit**

```bash
git add events.py tower_bot.py sinks/log.py sinks/tui.py tests/test_runs.py \
    tests/test_bot_reporting.py tests/test_log_sink.py tests/test_tui_sink.py
git commit -m "feat: read wave, coins and tier off the death modal"
```

---

### Task 12: Choose the strategy from the CLI

**Files:**
- Modify: `tower_bot.py:248-280` (`parse_args`), `tower_bot.py:324-380` (`main`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `affordability.DigitAffordability`, `affordability.BrightnessAffordability`, `digits.AtlasCache`
- Produces: `--affordability {digits,brightness}` (default `digits`), with automatic degradation when no atlas is present

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_affordability_defaults_to_digits() -> None:
    assert tower_bot.parse_args([]).affordability == "digits"


def test_affordability_can_be_forced_to_brightness() -> None:
    args = tower_bot.parse_args(["--affordability", "brightness"])
    assert args.affordability == "brightness"


def test_digits_without_an_atlas_degrades_to_brightness(tmp_path, caplog) -> None:
    """An unlabelled atlas must not stop the bot - phase 2 behaviour is the
    floor, not an error."""
    check = tower_bot.build_affordability("digits", atlas_root=tmp_path)
    assert isinstance(check, BrightnessAffordability)
    assert "atlas" in caplog.text.lower()


def test_digits_with_an_atlas_uses_digits(tmp_path) -> None:
    build_synthetic_atlas(tmp_path / "price")
    build_synthetic_atlas(tmp_path / "wallet")
    build_synthetic_atlas(tmp_path / "modal")
    check = tower_bot.build_affordability("digits", atlas_root=tmp_path)
    assert isinstance(check, DigitAffordability)


def test_brightness_is_used_even_when_an_atlas_exists(tmp_path) -> None:
    build_synthetic_atlas(tmp_path / "price")
    check = tower_bot.build_affordability("brightness", atlas_root=tmp_path)
    assert isinstance(check, BrightnessAffordability)
```

Add to the file's imports: `from tests.test_digits import build_synthetic_atlas` and `from affordability import BrightnessAffordability, DigitAffordability`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -k affordability -v`
Expected: FAIL with `AttributeError: 'Namespace' object has no attribute 'affordability'`

- [ ] **Step 3: Add the flag and the factory**

`build_affordability` annotates `atlas_root: Path | None`, and `tower_bot.py`
does not import `Path`. Add `from pathlib import Path` to its imports first.

In `parse_args`:

```python
    parser.add_argument(
        "--affordability", choices=("digits", "brightness"), default="digits",
        help=(
            "how to decide an upgrade is buyable: read the numbers (default, "
            "exact) or compare brightness (phase 2 heuristic). digits falls "
            "back to brightness on its own when no atlas is built"
        ),
    )
```

Add the factory at module level in `tower_bot.py`:

```python
def build_affordability(
    strategy: str, atlas_root: Path | None = None
) -> AffordabilityCheck:
    """Pick the affordability check, degrading when digits are unavailable.

    Digits need a labelled atlas that only exists once someone has run
    build_atlas.py. Without one, fall back rather than fail: the bot's
    phase-2 behaviour is the floor, and a bot that refuses to start is worse
    than one that guesses at brightness like it did last week.
    """
    if strategy == "brightness":
        return BrightnessAffordability()

    cache = digits.AtlasCache(atlas_root if atlas_root is not None else config.ATLAS_DIR)
    missing = [name for name in digits.SIZE_CLASSES if cache.get(name) is None]
    if missing:
        logger.warning(
            "no glyph atlas for %s - falling back to brightness affordability. "
            "Run: uv run build_atlas.py --size-class <name>",
            ", ".join(missing),
        )
        return BrightnessAffordability()

    return DigitAffordability(digits.NumberReader(cache), BrightnessAffordability())
```

In `main`, build the check and hand it to `TowerBot`:

```python
    affordability = build_affordability(args.affordability)
    bot = TowerBot(
        device=device,
        templates=templates,
        bus=bus,
        affordability_check=affordability,
        auto_navigate=args.auto_navigate,
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Verify against the live emulator**

```bash
uv run tower_bot.py --debug-scores          # still works
uv run tower_bot.py --once                  # names the screen, reports the wallet
uv run tower_bot.py --affordability brightness --once   # phase 2 path intact
```

With a run live, confirm the log line for a scan carries a wallet number and that a tap reports a price.

- [ ] **Step 6: Commit**

```bash
git add tower_bot.py tests/test_cli.py
git commit -m "feat: select the affordability strategy from the CLI"
```

---

## Done when

- `uv run pytest -q` passes with no emulator attached.
- `tests/test_atlas_real.py` reads the committed fixtures' wallet, wave, coins and tier correctly — the atlas is proven against real frames, not just synthetic ones.
- `uv run tower_bot.py --once` reports a wallet number while a run is live.
- An upgrade the wallet cannot cover is skipped with `reason=unaffordable`, distinct from `dimmed`.
- `RunEnded` carries wave, coins and tier after a death.
- With `templates/atlas/` removed, the bot still starts and falls back to brightness with a warning.
- The greyed-out brightness measurement from Task 1 Step 4 is recorded in the spec's section 13 — or its absence is, with the reason.

## Follow-on plans

- **Plan 3** — phase 4: SQLite persistence (`sinks/store.py`), FastAPI + SSE dashboard (`web/app.py`). `RunEnded` now carries everything its schema needs.
- **Plan 4** — phase 5: README rewrite, including the brightness section this plan supersedes and the note that templates must be cut from a lit in-run frame.
