"""Read text off the screen.

    frame  ->  engine  ->  drop low confidence  ->  TextBox

Every entry point returns empty or None rather than raising. A bad read must
degrade the bot, never stop the scan loop - the same rule digits.py follows.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

import config
from config import Rect
from device import Image

logger = logging.getLogger("tower_bot.ocr")

_engine: Any | None = None
# One lock guarding construction AND inference. The bot and the web server
# share a process, and the library makes no thread-safety promise worth
# betting a purchase on.
_lock = threading.Lock()


@dataclass(frozen=True)
class TextBox:
    text: str
    confidence: float
    rect: Rect


def _engine_or_none() -> Any | None:
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
        except Exception:
            logger.exception("could not build the OCR engine")
            return None
    return _engine


def read(screen: Image | None) -> tuple[TextBox, ...]:
    """Every text box on `screen` that clears the confidence floor."""
    if screen is None:
        return ()
    with _lock:
        engine = _engine_or_none()
        if engine is None:
            return ()
        try:
            result, _elapsed = engine(screen)
        except Exception:
            logger.exception("OCR failed on a %s frame", getattr(screen, "shape", "?"))
            return ()

    boxes: list[TextBox] = []
    try:
        for box, text, confidence in result or ():
            if confidence < config.OCR_CONFIDENCE_FLOOR:
                logger.debug("dropped %r at confidence %.3f", text, confidence)
                continue
            xs = [int(point[0]) for point in box]
            ys = [int(point[1]) for point in box]
            boxes.append(
                TextBox(
                    text=text.strip(),
                    confidence=float(confidence),
                    rect=Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)),
                )
            )
    except Exception:
        # Same rule as the engine call above: a malformed result shape must
        # degrade to "nothing read", never raise out of the scan loop - but
        # a caught exception that leaves no trace is its own defect.
        logger.exception("could not turn the OCR result into boxes")
        return ()
    return tuple(boxes)


# fullmatch() is itself the anchor - a leading/trailing ^/$ in the pattern
# would be a no-op. A partial match on "0.00/sec" or "x1.20" would report a
# stat value as a price; refusing is always safe, while a wrong number is not.
_NUMBER = re.compile(r"\$?(\d+(?:\.\d+)?)([KMB])?")
_SUFFIXES = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_number(text: str) -> int | None:
    """A price or balance, or None if this is not one."""
    match = _NUMBER.fullmatch(text.strip())
    if match is None:
        return None
    digits, suffix = match.groups()
    # round(), not int(): float(digits) * multiplier is not always exact
    # ("2.01" * 1000 lands a hair under 2010), and int() truncates toward
    # zero instead of correcting for it. This module's contract is to refuse
    # rather than guess - a rounding-induced off-by-one is exactly the kind
    # of silently wrong number that contract exists to prevent.
    return round(float(digits) * _SUFFIXES.get(suffix or "", 1))
