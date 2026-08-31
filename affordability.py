"""Is this upgrade actually buyable right now?

Phase 2 answers with brightness. Phase 3 replaces it with reading the wallet
and the price, which is exact. The protocol is the seam that makes the swap a
one-line change in the bot.
"""

from __future__ import annotations

from typing import Protocol

import config
import digits
import vision
from device import Image


class AffordabilityCheck(Protocol):
    # What the last affordable() call learned about the numbers, for the
    # Tapped event. Brightness never learns anything and leaves both None.
    last_price: int | None
    last_wallet: int | None

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

    # Brightness reads no numbers, so it has none to report.
    last_price: int | None = None
    last_wallet: int | None = None

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


class DigitAffordability:
    """Compare the wallet against the price. Exact, where brightness guesses.

    The wallet is read once per scan by the bot and pushed in, rather than
    re-read per action: it is the same number for all four upgrades, and the
    protocol hands us only the matched upgrade label, not the IN_RUN anchor
    the wallet region is measured from.

    Every failure degrades to `fallback` rather than blocking. If segmentation
    proves unreliable, brightness affordability stays and only the dashboard's
    numbers are lost.
    """

    def __init__(
        self,
        reader: digits.NumberReader,
        fallback: AffordabilityCheck | None = None,
    ) -> None:
        self._reader = reader
        self._fallback: AffordabilityCheck = fallback or BrightnessAffordability()
        self.wallet: int | None = None
        self.last_price: int | None = None
        self.last_wallet: int | None = None

    def affordable(
        self,
        screen: Image,
        match: vision.Match,
        template: Image,
        action: config.Action,
    ) -> tuple[bool, str]:
        price = self._reader.read(screen, config.PRICE_REGION, match.top_left, "price")
        self.last_price = price
        self.last_wallet = self.wallet

        if price is None or self.wallet is None:
            return self._fallback.affordable(screen, match, template, action)

        if self.wallet < price:
            return False, f"wallet {self.wallet} < price {price}"
        return True, ""
