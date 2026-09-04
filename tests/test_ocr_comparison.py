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
