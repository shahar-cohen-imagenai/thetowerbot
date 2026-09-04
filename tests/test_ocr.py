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
