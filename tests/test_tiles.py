"""Tile detection against the committed fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

import ocr
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
    ("Health", 130, 560),
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
    assert rows["Critical Factor"].price == 10


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
