"""The menu header, against committed captures.

The header is white text on a LIGHT panel, unlike every other number the bot
reads, which is light text on a dark one. At the shared default threshold the
panel survives binarisation and glues adjacent glyphs together: "1.77K"
segments as 1 . 77 K, and a merged span matches nothing, so the read fails.
"""

from __future__ import annotations

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


# NumberReader.read is all-or-nothing: one glyph the atlas cannot match fails
# the WHOLE read and returns None, never a partial or guessed number (see
# digits.NumberReader.read's docstring). That is what makes an incomplete
# atlas SAFE rather than merely inconvenient - a balance the bot could not
# fully read comes back as None, and nothing downstream can approve a
# purchase against a balance it never actually read. The two tests below
# hold the atlas to exactly what the committed fixtures can prove, and
# separately document - without silently passing - what a live session
# still needs to add before the atlas covers real play.

# Every glyph the five committed fixtures' coin/gem counters show between
# them: WORKSHOP's "1.77K" and "40", CARDS's "78" and "40", MAIN_MENU's "78"
# and "0". No fixture balance has passed through 2, 3, 5, 6, 9, or reached
# the millions/billions suffixes.
FIXTURE_GLYPHS = {"0", "1", "4", "7", "8", ".", "K"}


def test_header_atlas_has_every_glyph_the_fixtures_contain() -> None:
    """The real completeness gate for this plan: every other test in it reads
    a committed fixture, and these seven glyphs are everything those
    fixtures show, so they are sufficient for the tests that exist today."""
    atlas = digits.AtlasCache().get("header")
    assert atlas is not None, "run tools/harvest_header_glyphs.py"
    assert atlas.labels == FIXTURE_GLYPHS


@pytest.mark.skip(
    reason=(
        "header atlas is missing 2 3 5 6 9 M B - no committed fixture's "
        "balance has passed through those digits or reached millions/"
        "billions. Harvest them from a live play session with "
        "`uv run build_atlas.py --size-class header --frames 20`, label "
        "them, then delete this skip marker."
    )
)
def test_header_atlas_is_not_yet_complete_for_live_play() -> None:
    """Once a play session supplies the missing glyphs, the header atlas
    should carry the full set the live game can render - not just what the
    fixtures happen to show."""
    atlas = digits.AtlasCache().get("header")
    assert atlas is not None
    required = set("0123456789") | {".", "K", "M", "B"}
    missing = required - atlas.labels
    assert not missing, f"header atlas is missing {sorted(missing)}"


# page, expected coins, expected gems - see the task brief for how each
# value was hand-verified against its fixture.
HEADER_READ_CASES: dict[str, tuple[str, int, int]] = {
    "menu_workshop_attack": ("WORKSHOP", 1770, 40),
    "menu_workshop_defense": ("WORKSHOP", 1770, 40),
    "menu_workshop_utility": ("WORKSHOP", 1770, 40),
    "menu_cards": ("CARDS", 78, 40),
    "menu_main": ("MAIN_MENU", 78, 0),
}


@pytest.mark.parametrize("fixture,case", HEADER_READ_CASES.items())
def test_header_reads_a_suffixed_balance(fixture: str, case: tuple[str, int, int]) -> None:
    """The README's known gap, closed. 1.77K is the coin balance on the three
    workshop fixtures; cards and main menu prove the same atlas also reads a
    plain, unsuffixed balance correctly."""
    page, expected_coins, expected_gems = case
    img = cv2.imread(str(FIXTURES / f"{fixture}.png"), cv2.IMREAD_COLOR)
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    _, top_left = vision.best_score(img, cache.get(config.PAGE_ANCHORS[page]))

    reader = digits.NumberReader()
    coins_region, gems_region = config.HEADER_REGIONS[page]
    assert reader.read(img, coins_region, top_left, "header") == expected_coins
    assert reader.read(img, gems_region, top_left, "header") == expected_gems
