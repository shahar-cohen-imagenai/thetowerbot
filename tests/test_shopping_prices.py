"""Reading the prices in the game's menus.

Task 5a proved the price REGIONS land on the right pixels. This proves the
numbers inside them can actually be read.

Menu prices render at three sizes (row 22px, unlock tile 28px, cards button
31px) and `digits.Atlas` holds one image per label, so each glyph is stored at
its LARGEST instance: `Atlas.match` resizes the candidate to the template, and
downscaling a big candidate into a small template is the one direction
measured below threshold (tile->row scored 0.67 against a 0.70 bar).
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

import config
import digits
import ocr
import tiles
import vision
from tools import harvest_menu_glyphs

FIXTURES = Path(__file__).parent / "fixtures"


def frame(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    return img


@pytest.fixture
def cache():
    return vision.TemplateCache(config.TEMPLATE_DIR)


@pytest.fixture
def reader():
    return digits.NumberReader()


# Every price the committed fixtures show, hand-verified against the fixture
# it came from.
WORKSHOP_PRICE_CASES: tuple[tuple[str, str, int], ...] = (
    ("menu_workshop_attack", "Damage", 30),
    ("menu_workshop_attack", "Attack Speed", 30),
    ("menu_workshop_attack", "Critical Chance", 50),
    ("menu_workshop_attack", "Critical Factor", 50),
    ("menu_workshop_attack", "Unlock Range Upgrades", 50),
    ("menu_workshop_defense", "Health", 30),
    ("menu_workshop_defense", "Health Regen", 30),
    ("menu_workshop_defense", "Unlock Defense Upgrades", 75),
    ("menu_workshop_utility", "Unlock Cash Bonuses", 40),
)


def _recorded(stem: str) -> tuple[ocr.TextBox, ...]:
    raw = json.loads((FIXTURES / "ocr" / f"{stem}.json").read_text())
    return tuple(
        ocr.TextBox(text=entry["text"], confidence=entry["confidence"],
                    rect=tiles.Rect(*entry["rect"]))
        for entry in raw
    )


@pytest.mark.parametrize("fixture,row_name,expected", WORKSHOP_PRICE_CASES)
def test_every_workshop_price_reads_exactly(fixture: str, row_name: str, expected: int) -> None:
    """A row is addressed by name now (spec §7): the price comes from
    `tiles.read_rows`/`row.price`, not from a template-matched anchor plus
    `config.PRICE_REGIONS`. Uses the recorded OCR fixtures rather than a live
    OCR engine - deterministic, same pattern as test_tiles.py - since this is
    a test of the tile-to-row assignment and price parse, not of OCR itself.
    """
    screen = frame(fixture)
    rows = {row.name: row for row in tiles.rows_from(_recorded(fixture), tiles.find_tiles(screen))}
    assert row_name in rows, f"{row_name} not found on {fixture}"
    assert rows[row_name].price == expected, (
        f"{fixture} {row_name}: expected {expected}, got {rows[row_name].price}"
    )


def test_ocr_harvest_reaches_the_same_glyph_shapes_as_the_template_harvest(monkeypatch) -> None:
    """tools/harvest_menu_glyphs.py's harvest_workshop no longer
    template-matches config.WORKSHOP_ROWS and crops config.PRICE_REGIONS
    (both deleted, spec §7 plus its AMENDMENT) - it reads `tiles.read_rows`
    and crops `row.price_rect`, the same way the bot itself finds a price
    now. This proves that trade lost nothing digit-shape-wise: every glyph
    the rewritten harvester crops out of the workshop fixtures still matches
    a label already in the committed menu atlas, and the matched set is
    exactly the digits WORKSHOP_PRICE_CASES' prices are built from (30, 30,
    50, 50, 50, 30, 30, 75, 40 -> 0, 3, 4, 5, 7).

    Uses the recorded OCR fixtures for the same determinism reason as the
    price-read test above, monkeypatching `tiles.read_rows` as seen by the
    harvester module - the same technique tests/test_shopping.py uses to
    drive shopping.py off a canned Row without a live OCR engine.
    """
    kept: list = []
    for stem in ("menu_workshop_attack", "menu_workshop_defense", "menu_workshop_utility"):
        screen = frame(stem)
        monkeypatch.setattr(
            harvest_menu_glyphs.tiles, "read_rows",
            lambda s, stem=stem: tiles.rows_from(_recorded(stem), tiles.find_tiles(s)),
        )
        harvest_menu_glyphs.harvest_workshop(screen, kept)

    assert kept, "harvest_workshop produced no glyphs"

    atlas = digits.AtlasCache().get("menu")
    assert atlas is not None, "run tools/harvest_menu_glyphs.py"
    matched = {atlas.match(glyph) for glyph in kept}
    assert None not in matched, (
        "a glyph the OCR harvest cropped does not match any labelled atlas entry"
    )
    assert matched == {"0", "3", "4", "5", "7"}, matched


CARD_PRICE_CASES: tuple[tuple[str, int], ...] = (
    ("x1", 20),
    ("x10", 200),
)


@pytest.mark.parametrize("button_name,expected", CARD_PRICE_CASES)
def test_every_card_price_reads_exactly(
    button_name: str, expected: int, cache, reader
) -> None:
    screen = frame("menu_cards")
    match = vision.locate_template(screen, cache.get(config.CARD_BUTTONS[button_name]), 0.9)
    assert match is not None, f"{button_name} not found"

    price = reader.read(screen, config.CARD_PRICE_REGION, match.top_left, "menu")
    assert price == expected, f"cards {button_name}: expected {expected}, got {price}"


# Every glyph the committed fixtures' price regions supply between them: the
# union of digits across all eleven prices above (0 2 3 4 5 7), plus the two
# currency icons.
FIXTURE_GLYPHS = {"0", "2", "3", "4", "5", "7", "©", digits.GEM}


def test_menu_atlas_has_every_glyph_the_fixtures_contain() -> None:
    """The real completeness gate for this task, same pattern as the header
    atlas's equivalent test: every other test in this file reads a committed
    fixture, and this set is everything those fixtures show, so it is
    sufficient for the tests that exist today."""
    atlas = digits.AtlasCache().get("menu")
    assert atlas is not None, "run tools/harvest_menu_glyphs.py"
    assert atlas.labels == FIXTURE_GLYPHS


@pytest.mark.skip(
    reason=(
        "menu atlas is missing 1 6 8 9 - no committed fixture's workshop or "
        "cards price contains them. A price containing one of them reads "
        "None today and the purchase is skipped, which is the safe outcome "
        "(see digits.NumberReader.read's all-or-nothing docstring). Harvest "
        "them once a live account shows a price using one of these digits, "
        "label them by hand (do not trust the auto-labeller's argmax), then "
        "delete this skip marker."
    )
)
def test_menu_atlas_is_not_yet_complete_for_live_play() -> None:
    """Once a live price supplies the missing digits, the menu atlas should
    carry the full digit set - not just what the fixtures happen to show."""
    atlas = digits.AtlasCache().get("menu")
    assert atlas is not None
    missing = set("0123456789") - atlas.labels
    assert not missing, f"menu atlas is missing {sorted(missing)}"


def test_menu_is_offered_to_the_atlas_tools_but_not_to_affordability() -> None:
    """build_affordability downgrades the WHOLE bot to brightness if any
    SIZE_CLASSES entry is unbuilt. The menu class must not be able to do
    that - an unbuilt menu atlas should only disable menu shopping."""
    assert "menu" in digits.ALL_SIZE_CLASSES
    assert "menu" not in digits.SIZE_CLASSES


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30©", 30),
        ("20◇", 20),
    ],
)
def test_parse_number_discards_both_currency_icons(text: str, expected: int) -> None:
    """CARD_PRICE_REGION deliberately includes its currency icon (see its own
    comment in config.py), so the parser has to be able to see and discard
    it - pinned directly, independent of the image pipeline above. The coin
    icon is pinned alongside it for the same reason even though workshop
    prices no longer route through this parser (they come off OCR text via
    ocr.parse_number instead, spec §7's AMENDMENT): digits.parse_number is a
    general-purpose parser and this is its only direct pin of coin-discarding
    behaviour."""
    assert digits.parse_number(text) == expected
