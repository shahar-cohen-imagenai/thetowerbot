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

from pathlib import Path

import cv2
import pytest

import config
import digits
import vision

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


@pytest.mark.parametrize("fixture,row_name,expected", WORKSHOP_PRICE_CASES)
def test_every_workshop_price_reads_exactly(
    fixture: str, row_name: str, expected: int, cache, reader
) -> None:
    screen = frame(fixture)
    template_path, layout = config.WORKSHOP_ROWS[row_name]
    match = vision.locate_template(screen, cache.get(template_path), 0.9)
    assert match is not None, f"{row_name} not found on {fixture}"

    price = reader.read(screen, config.PRICE_REGIONS[layout], match.top_left, "menu")
    assert price == expected, f"{fixture} {row_name}: expected {expected}, got {price}"


def test_row_region_excludes_the_tile_border(cache) -> None:
    """Pins the PRICE_REGIONS["row"] width trim (250 -> 225).

    At the original width the crop's right edge caught a few pixels of the
    upgrade tile's own border - a fixed sliver that segments as a fourth
    "glyph" alongside the two digits and the coin. That sliver matches no
    real atlas entry (it scores 0.0 against every one of them, nowhere near
    GLYPH_MATCH_THRESHOLD), so Atlas.match returns None for it and the whole
    price fails under the all-or-nothing rule - a safe failure, but a total
    one. Exactly 3 spans (2 digits + the coin) is the signal that the border
    is excluded; a regression back toward width 250 would show up here as 4.
    """
    screen = frame("menu_workshop_attack")
    template_path, _ = config.WORKSHOP_ROWS["Damage"]
    match = vision.locate_template(screen, cache.get(template_path), 0.9)
    assert match is not None

    patch = digits.crop(screen, config.PRICE_REGIONS["row"], match.top_left)
    spans = digits.glyph_spans(digits.binarize(patch, digits.threshold_for("menu")))
    assert len(spans) == 3, f"expected 2 digits + coin, segmented {len(spans)}"


def test_widening_the_row_region_reintroduces_the_border_and_fails_the_read(cache, reader) -> None:
    """The regression this trim guards against, demonstrated directly: widen
    PRICE_REGIONS["row"] back toward its original 250 and the border sliver
    comes back into the crop, segments as an unmatched fourth glyph, and
    fails the whole read - even though the two real digits and the coin are
    still sitting at the exact same pixels they were at the narrower width.
    """
    screen = frame("menu_workshop_attack")
    template_path, _ = config.WORKSHOP_ROWS["Damage"]
    match = vision.locate_template(screen, cache.get(template_path), 0.9)
    assert match is not None

    narrow = config.PRICE_REGIONS["row"]
    widened = config.Region(dx=narrow.dx, dy=narrow.dy, w=250, h=narrow.h)

    assert reader.read(screen, narrow, match.top_left, "menu") == 30
    assert reader.read(screen, widened, match.top_left, "menu") is None


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
    """Both price regions deliberately include their currency icon (see
    config.PRICE_REGIONS's and config.CARD_PRICE_REGION's own comments), so
    the parser has to be able to see and discard each one - pinned directly,
    independent of the image pipeline above."""
    assert digits.parse_number(text) == expected
