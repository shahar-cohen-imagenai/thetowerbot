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
