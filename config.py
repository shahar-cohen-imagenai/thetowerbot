"""Runtime configuration for the Tower bot.

Keep every tunable value here so the bot logic stays free of magic numbers.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

# --- ADB connection -------------------------------------------------------
ADB_HOST: str = "127.0.0.1"
ADB_PORT: int = 5037  # adb *server* port (not the device port)

# The emulator's ADB endpoint. Android Studio AVDs listen on <console_port + 1>,
# so the first running emulator (emulator-5554) is reachable at 127.0.0.1:5555.
DEVICE_HOST: str = "127.0.0.1"
DEVICE_PORT: int = 5555

# --- Loop timing ----------------------------------------------------------
SCAN_INTERVAL_SECONDS: float = 2.0
# Minimum delay between two clicks on the *same* template, so a slow UI
# animation does not cause a burst of taps on a button that is already pressed.
CLICK_COOLDOWN_SECONDS: float = 1.0

# --- Vision ---------------------------------------------------------------
TEMPLATE_DIR: Path = Path(__file__).parent / "templates"
DEFAULT_THRESHOLD: float = 0.8

# cv2.TM_CCOEFF_NORMED normalises out mean and variance, so it is blind to
# brightness: a greyed-out "can't afford it yet" button still scores ~1.0.
# Guard against that by also requiring the matched region to be about as bright
# as the template, which was cropped while the button was affordable.
# The value is a ratio of mean grey level; 0.0 disables the check.
DEFAULT_BRIGHTNESS_RATIO: float = 0.75


class Region(NamedTuple):
    """A rectangle expressed relative to a matched anchor's top-left.

    Never absolute. The death modal shifts ~46px vertically depending on
    whether the "New Highest Wave!" line is present, so a hardcoded y would
    read the wrong row half the time. dx/dy may be negative: the anchor is
    not always above-left of the number it locates.
    """

    dx: int
    dy: int
    w: int
    h: int


class Action(NamedTuple):
    """One template the bot looks for, in priority order."""

    name: str
    template: str  # file name inside TEMPLATE_DIR
    threshold: float = DEFAULT_THRESHOLD
    # Minimum matched-region brightness, as a fraction of the template's own.
    # Keeps the bot from tapping upgrades it cannot afford. 0.0 disables.
    brightness_ratio: float = DEFAULT_BRIGHTNESS_RATIO


# Evaluated top to bottom on every scan. Add rows as you capture more buttons.
# Templates crop the *label* only (not the value/price boxes), because those
# numbers change on every purchase and would break the match.
ACTIONS: tuple[Action, ...] = (
    Action(name="Attack Speed", template="upgrade_attack_speed.png", threshold=0.9),
    Action(name="Critical Chance", template="upgrade_critical_chance.png", threshold=0.9),
    Action(name="Damage", template="upgrade_damage.png", threshold=0.9),
    Action(name="Critical Factor", template="upgrade_critical_factor.png", threshold=0.9),
)

# --- Screen recognition -----------------------------------------------------
# Anchors are small crops unique to one screen. Measured separation on the
# golden fixtures: 1.000 on the correct screen, <=0.462 on every wrong one,
# so 0.8 has a wide margin in both directions.
ANCHOR_THRESHOLD: float = 0.8

# A transition is only declared after this many consecutive identical
# readings. Capture lands inside the death modal's fade animation (the same
# region measures 0.74 mid-fade and 0.28 fully dimmed), and without debounce
# those frames produce phantom transitions that corrupt run boundaries.
SCREEN_CONFIRMATIONS: int = 2

SCREEN_ANCHORS: dict[str, str] = {
    "MAIN_MENU": "screens/main_menu.png",
    "IN_RUN": "screens/in_run.png",
    "GAME_OVER": "screens/game_over.png",
}

# --- Unknown-screen snapshots ---------------------------------------------
UNKNOWN_DIR: Path = Path(__file__).parent / "unknown"
UNKNOWN_MIN_INTERVAL: float = 30.0
UNKNOWN_KEEP: int = 50

# --- Resolution guard ------------------------------------------------------
# Templates are not scale-invariant. A different emulator resolution
# invalidates every one of them.
EXPECTED_RESOLUTION: tuple[int, int] = (1080, 2400)

# --- Auto-navigation ------------------------------------------------------
# Buttons are located by template match, never by fixed coordinates: the
# death modal shifts ~46px vertically depending on whether the
# "New Highest Wave!" line is present.
NAVIGATION_COOLDOWN_SECONDS: float = 3.0
NAV_BUTTONS: dict[str, tuple[str, str]] = {
    "GAME_OVER": ("RETRY", "buttons/retry.png"),
    "MAIN_MENU": ("BATTLE", "buttons/battle.png"),
}

# --- Digit reading --------------------------------------------------------
# Numbers are light glyphs on a dark panel. Binarise, split by column gaps,
# match each glyph against a per-size-class atlas.
ATLAS_DIR: Path = TEMPLATE_DIR / "atlas"

# Grey level above which a pixel counts as glyph rather than background.
DIGIT_BINARY_THRESHOLD: int = 140
# A glyph must match an atlas entry at least this well to be accepted.
GLYPH_MATCH_THRESHOLD: float = 0.7
# Narrowest run of lit columns still treated as a glyph. The decimal point is
# the narrowest real glyph, so raising this silently turns 1.5K into 15K.
GLYPH_MIN_WIDTH: int = 2

# Every region is relative to a matched anchor - see config.Region.
# NOT YET CALIBRATED: these are zeros until measured against live frames with
# tools/crop_preview.py. Until then every digit read returns None and the bot
# falls back to brightness affordability, which is phase 2's behaviour.
WALLET_REGION: Region = Region(dx=0, dy=0, w=0, h=0)        # from IN_RUN anchor
PRICE_REGION: Region = Region(dx=0, dy=0, w=0, h=0)         # from an upgrade label
MODAL_WAVE_REGION: Region = Region(dx=0, dy=0, w=0, h=0)    # from GAME_OVER anchor
MODAL_COINS_REGION: Region = Region(dx=0, dy=0, w=0, h=0)   # from GAME_OVER anchor
MODAL_TIER_REGION: Region = Region(dx=0, dy=0, w=0, h=0)    # from GAME_OVER anchor
