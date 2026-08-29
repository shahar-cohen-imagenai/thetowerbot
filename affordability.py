"""Is this upgrade actually buyable right now?

Phase 2 answers with brightness. Phase 3 replaces it with reading the wallet
and the price, which is exact. The protocol is the seam that makes the swap a
one-line change in the bot.
"""

from __future__ import annotations

from typing import Protocol

import config
import vision
from device import Image


class AffordabilityCheck(Protocol):
    def affordable(
        self, screen: Image, match: vision.Match, template: Image
    ) -> tuple[bool, str]:
        """Return (ok, human-readable detail)."""
        ...


class BrightnessAffordability:
    """Reject regions dimmer than the template they matched.

    TM_CCOEFF_NORMED normalises out brightness, so a dimmed button scores the
    same as a lit one. Measured on the fixtures: 1.00 lit, 0.28 dimmed.

    NOTE: this is verified against modal-dimming only. The greyed-out
    "cannot afford" state was never captured, so its separation is unproven.
    Phase 3 removes the dependency by reading the numbers instead.
    """

    def __init__(self, ratio: float = config.DEFAULT_BRIGHTNESS_RATIO) -> None:
        self.ratio = ratio

    def affordable(
        self, screen: Image, match: vision.Match, template: Image
    ) -> tuple[bool, str]:
        if self.ratio <= 0.0:
            return True, ""
        measured = vision.brightness_ratio(screen, match, template)
        if measured < self.ratio:
            return False, f"brightness {measured:.2f} < {self.ratio:.2f}"
        return True, ""
