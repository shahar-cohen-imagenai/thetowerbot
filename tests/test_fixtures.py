"""Fixtures are golden data. If these fail, a template or capture drifted."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"
EXPECTED_RESOLUTION = (1080, 2400)  # width, height


@pytest.mark.parametrize(
    "name", ["main_menu", "in_run_lit", "game_over", "game_over_fade"]
)
def test_fixture_resolution(name: str) -> None:
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    height, width = img.shape[:2]
    assert (width, height) == EXPECTED_RESOLUTION


def test_upgrade_templates_are_lit_not_dimmed() -> None:
    """Regression guard for the originating bug.

    The original templates were cropped from the death modal, where every
    label sits in a dimmed backdrop. A lit crop of these labels measures
    ~50-58 mean grey; the dimmed equivalent measures ~14. If someone re-cuts
    a template from a game-over frame, this catches it.
    """
    for path in sorted(TEMPLATES.glob("upgrade_*.png")):
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        assert img is not None, f"unreadable template: {path}"
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean()
        assert grey > 40.0, (
            f"{path.name} has mean grey {grey:.2f} - it looks like it was "
            "cropped from a dimmed frame. Re-cut it from a live run."
        )
