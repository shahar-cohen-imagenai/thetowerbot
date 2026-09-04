"""The OCR reader: number parsing, and a few real reads."""

from __future__ import annotations

import sys
import types
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
        # Regression: float(digits) * multiplier is not always exact -
        # 2.01 * 1000 lands at 2009.9999999999998, which int() truncates to
        # 2009. round() is required to get the correct 2010.
        ("2.01K", 2010),
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


def test_a_failed_engine_build_is_not_retried(monkeypatch):
    """A broken wheel must cost one traceback, not one per scan.

    There is no startup gate until Phase 2, so a retry-forever build would
    write a traceback into the log every couple of seconds - the same log
    the Phase 1 A/B evidence is read out of.
    """
    builds = 0

    class Broken(types.ModuleType):
        def __getattr__(self, name):
            nonlocal builds
            builds += 1
            raise ImportError("no wheel for this platform")

    # monkeypatch restores both, so a real engine built by the test above
    # survives this one.
    monkeypatch.setattr(ocr, "_engine", None)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", Broken("rapidocr_onnxruntime"))

    assert ocr._engine_or_none() is None
    assert ocr._engine_or_none() is None
    assert ocr._engine_or_none() is None
    assert builds == 1, "construction must be attempted once, not once per scan"
