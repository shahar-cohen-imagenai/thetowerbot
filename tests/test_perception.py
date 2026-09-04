from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

import config
import ocr

FIXTURES = Path(__file__).parent / "fixtures"


def recorded(name: str) -> tuple[ocr.TextBox, ...]:
    return tuple(ocr.TextBox(b["text"], b["confidence"], config.Rect(*b["rect"]))
                 for b in json.loads((FIXTURES / "ocr" / f"{name}.json").read_text()))


def test_battle_values_are_distinct_from_costs_and_combat() -> None:
    from perception import parse_frame
    frame = cv2.imread(str(FIXTURES / "in_run_lit.png"))
    result = parse_frame(frame, recorded("in_run_lit"), "battle", now=100)
    rows = {r.upgrade_id: r for r in result.rows}
    assert result.category == "ATTACK"
    assert rows["critical_chance"].value == 1
    assert rows["critical_chance"].price == 4
    assert rows["critical_factor"].value == 1.2
    assert result.combat["wave"] == 1
    assert result.combat["health"] == 5
    assert result.combat["max_health"] == 5
    assert result.combat["enemy_damage"] == 1.18
    # The OCR read this as reversed "68 $". Refusing is better than spending.
    assert result.cash is None


@pytest.mark.parametrize("text,want", [("1.20K",1200),("x1.20",1.2),("51%",51),
                                      ("2.3T",2.3e12),("10.0s",10),("5 sec",5),
                                      ("MAX",None),("NaN",None)])
def test_stat_number_handles_units_without_guessing(text: str, want: float | None) -> None:
    from perception import stat_number
    assert stat_number(text) == want


def test_duration_stat_is_not_a_purchase_price() -> None:
    from perception import price_number
    assert price_number("10.0s") is None


def test_max_and_lock_markers_do_not_become_part_of_upgrade_name() -> None:
    from perception import parse_frame
    frame = cv2.imread(str(FIXTURES / "in_run_lit.png"))
    boxes = tuple(ocr.TextBox("MAX" if b.text == "$4" else b.text, b.confidence, b.rect)
                  for b in recorded("in_run_lit"))
    result = parse_frame(frame, boxes, "battle", now=100)
    row = next(r for r in result.rows if r.upgrade_id == "critical_chance")
    assert row.status == "maxed"
    assert row.price is None


def test_workshop_unlock_keeps_its_own_identity() -> None:
    from perception import parse_frame
    frame = cv2.imread(str(FIXTURES / "menu_workshop_utility.png"))
    result = parse_frame(frame, recorded("menu_workshop_utility"), "workshop", now=100)
    assert result.rows[0].upgrade_id == "unlock_cash_bonuses"
    assert result.rows[0].price == 40
    assert result.rows[0].value is None


def test_wallet_crop_ocr_reads_recorded_balance_without_a_digit_atlas() -> None:
    from perception import read_cash
    frame = cv2.imread(str(FIXTURES / "in_run_lit.png"))
    assert read_cash(frame, (12, 1646)) == 89


def test_wallet_ocr_refuses_ambiguous_or_low_confidence_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    from perception import read_cash
    frame = cv2.imread(str(FIXTURES / "in_run_lit.png"))
    box = config.Rect(1, 1, 30, 30)
    monkeypatch.setattr(ocr, "read", lambda image: (ocr.TextBox("$89", .89, box),))
    assert read_cash(frame, (12, 1646)) is None
    monkeypatch.setattr(ocr, "read", lambda image: (ocr.TextBox("8", .99, box), ocr.TextBox("9", .99, box)))
    assert read_cash(frame, (12, 1646)) is None
