"""Digit reading. Fixtures are golden data - failures mean a capture drifted."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

import config
import digits

FIXTURES = Path(__file__).parent / "fixtures"


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
