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
        self,
        screen: Image,
        match: vision.Match,
        template: Image,
        action: config.Action,
    ) -> tuple[bool, str]:
        """Return (ok, human-readable detail).

        The whole `action` is passed, not just its brightness knob, so each
        implementation can read the per-action config it actually cares
        about: `brightness_ratio` here, price regions for the phase-3
        digit-reading check. The seam stays one line wide in the bot.
        """
        ...


class BrightnessAffordability:
    """Reject regions dimmer than the template they matched.

    TM_CCOEFF_NORMED normalises out brightness, so a dimmed button scores the
    same as a lit one. Measured on the fixtures: 1.00 lit, 0.28 dimmed.

    The threshold is `action.brightness_ratio` (default
    `config.DEFAULT_BRIGHTNESS_RATIO`), so a button whose lit and dimmed
    states sit closer together can be tuned on its own, and `0.0` disables
    the check for that one action.

    NOTE: this is verified against modal-dimming only. The greyed-out
    "cannot afford" state was never captured, so its separation is unproven.
    Phase 3 removes the dependency by reading the numbers instead.
    """

    def affordable(
        self,
        screen: Image,
        match: vision.Match,
        template: Image,
        action: config.Action,
    ) -> tuple[bool, str]:
        ratio = action.brightness_ratio
        if ratio <= 0.0:
            return True, ""
        measured = vision.brightness_ratio(screen, match, template)
        if measured < ratio:
            return False, f"brightness {measured:.2f} < {ratio:.2f}"
        return True, ""
