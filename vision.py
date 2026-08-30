"""Vision layer: template loading and matching.

    screen capture  ->  device.capture_screen()
    template match  ->  cv2.matchTemplate
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

import cv2

from device import Image

logger = logging.getLogger("tower_bot")


class Match(NamedTuple):
    """Where a template was found on screen, and how well it scored."""

    center: tuple[int, int]
    score: float
    top_left: tuple[int, int]


class TemplateCache:
    """Loads template images once and keeps them in memory."""

    def __init__(self, template_dir: Path) -> None:
        self._dir = template_dir
        self._cache: dict[str, Image] = {}

    def get(self, template_path: str | Path) -> Image:
        key = str(template_path)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        path = Path(template_path)
        if not path.is_absolute() and not path.exists():
            path = self._dir / path
        if not path.exists():
            raise FileNotFoundError(f"Template not found: {path}")

        template = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if template is None:
            raise ValueError(f"Could not read template image: {path}")

        self._cache[key] = template
        logger.debug("Loaded template %s (%dx%d)", path.name, template.shape[1], template.shape[0])
        return template


def best_score(screen: Image, template: Image) -> tuple[float, tuple[int, int]]:
    """Best match score and its top-left position, ignoring any threshold."""
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return float(max_val), max_loc


def locate_template(screen: Image, template: Image, threshold: float) -> Match | None:
    """Return the best match above threshold, or None."""
    screen_h, screen_w = screen.shape[:2]
    tpl_h, tpl_w = template.shape[:2]
    if tpl_h > screen_h or tpl_w > screen_w:
        logger.warning(
            "Template (%dx%d) is larger than the screen (%dx%d) - was it "
            "captured at a different emulator resolution?",
            tpl_w, tpl_h, screen_w, screen_h,
        )
        return None

    score, top_left = best_score(screen, template)
    if score < threshold:
        return None

    center = (top_left[0] + tpl_w // 2, top_left[1] + tpl_h // 2)
    return Match(center=center, score=score, top_left=top_left)


def mean_brightness(image: Image) -> float:
    """Mean grey level of ``image`` (0-255)."""
    return float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).mean())


def brightness_ratio(screen: Image, match: Match, template: Image) -> float:
    """Brightness of the matched region relative to the template's own.

    TM_CCOEFF_NORMED is blind to brightness, so a dimmed button scores the
    same as a lit one. This is what tells them apart.
    """
    tpl_h, tpl_w = template.shape[:2]
    x, y = match.top_left
    region = screen[y : y + tpl_h, x : x + tpl_w]
    template_level = mean_brightness(template)
    if template_level <= 0.0:  # all-black template: nothing to compare against
        return 1.0
    return mean_brightness(region) / template_level
