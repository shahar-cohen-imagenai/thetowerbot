"""Which screen is the game on?

Anchors are small crops unique to one screen. Every scan matches all of them
full-frame and takes the argmax; below ANCHOR_THRESHOLD the answer is UNKNOWN
and the bot holds.

Full-frame rather than region-of-interest is deliberate: ~100ms for three
anchors against a 2s scan interval is not worth optimising, and the death
modal moves vertically, so ROI padding would need tuning for no gain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import config
import vision
from device import Image


class ScreenState(str, Enum):
    MAIN_MENU = "MAIN_MENU"
    IN_RUN = "IN_RUN"
    GAME_OVER = "GAME_OVER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ScreenReading:
    """One frame's classification, with every anchor's score for debugging."""

    state: ScreenState
    confidence: float
    scores: dict[str, float]
    top_left: tuple[int, int] | None = None


def classify(
    screen: Image,
    cache: vision.TemplateCache,
    threshold: float = config.ANCHOR_THRESHOLD,
) -> ScreenReading:
    scores: dict[str, float] = {}
    positions: dict[str, tuple[int, int]] = {}

    for name, template_path in config.SCREEN_ANCHORS.items():
        score, top_left = vision.best_score(screen, cache.get(template_path))
        scores[name] = score
        positions[name] = top_left

    winner = max(scores, key=lambda name: scores[name])
    confidence = scores[winner]

    if confidence < threshold:
        return ScreenReading(ScreenState.UNKNOWN, confidence, scores)

    return ScreenReading(
        ScreenState(winner), confidence, scores, top_left=positions[winner]
    )


class ScreenTracker:
    """Debounces raw readings into confirmed screen transitions.

    Capture lands inside the death modal's fade animation, so a single frame
    is not trustworthy. A transition is declared only after `confirmations`
    consecutive identical readings; an interrupted run resets the count.
    """

    def __init__(self, confirmations: int = config.SCREEN_CONFIRMATIONS) -> None:
        self._confirmations = confirmations
        self.state = ScreenState.UNKNOWN
        self._pending: ScreenState | None = None
        self._streak = 0

    def observe(self, reading: ScreenReading) -> ScreenState | None:
        """Feed one reading. Returns the new state on a confirmed transition."""
        if reading.state is self.state:
            self._pending = None
            self._streak = 0
            return None

        if reading.state is self._pending:
            self._streak += 1
        else:
            self._pending = reading.state
            self._streak = 1

        if self._streak < self._confirmations:
            return None

        self.state = reading.state
        self._pending = None
        self._streak = 0
        return self.state
