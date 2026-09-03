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

# Per-size-class overrides. The in-run and modal numbers are light glyphs on
# a dark panel, which is what the 140 default is for. The menu header is the
# other way round - white text on a light purple bar - and at 140 the bar
# survives binarisation and bridges adjacent glyphs: "1.77K" segments as
# 1 . 77 K, and a merged span matches no atlas entry, so the whole read
# fails. Measured on menu_workshop_attack.png: 170-240 all segment correctly,
# so 200 sits in the middle of the plateau rather than on its edge.
DIGIT_BINARY_THRESHOLDS: dict[str, int] = {"header": 200}

# A glyph must match an atlas entry at least this well to be accepted.
GLYPH_MATCH_THRESHOLD: float = 0.7
# Narrowest run of lit columns still treated as a glyph. The decimal point is
# the narrowest real glyph, so raising this silently turns 1.5K into 15K.
GLYPH_MIN_WIDTH: int = 2

# Every region is relative to a matched anchor - see config.Region.
# Measured on a 1080x2400 capture; re-measure if the resolution ever changes.

# The in-run HUD, from the IN_RUN anchor at (12, 1646). Wide enough for the
# wallet to grow into "$ 12.34K" without clipping.
WALLET_REGION: Region = Region(dx=13, dy=-1484, w=230, h=72)

# The price box under an upgrade, from that upgrade's own matched LABEL - not
# from a screen anchor, because each of the four buttons has its own box. The
# same offset lands correctly on all four; verified against every one.
# Inset from the box border so a brighter theme cannot smear the projection.
PRICE_REGION: Region = Region(dx=252, dy=98, w=208, h=38)


def buy_point(anchor: tuple[int, int]) -> tuple[int, int]:
    """Where to tap to BUY the upgrade whose label matched at `anchor`.

    NOT the label's own centre. The label is itself a button - it opens an
    info panel that covers the screen - so a tap there buys nothing and
    blinds the next scan behind the overlay. The buy button is the square to
    the label's right.

    The point is derived from PRICE_REGION rather than measured separately:
    that offset already locates the price strip inside the buy square, from
    this same anchor, for all four upgrades. One calibration to keep correct
    instead of two that can drift apart.
    """
    return (
        anchor[0] + PRICE_REGION.dx + PRICE_REGION.w // 2,
        anchor[1] + PRICE_REGION.dy + PRICE_REGION.h // 2,
    )


# Death modal, from the GAME_OVER anchor at (330, 663). These crop the whole
# CENTRED line, not just its number: "Wave 1" grows to "Wave 137" and the
# digits shift left as it does, so a crop tight to the number would slide off
# it. Reading them therefore needs the label glyphs in the atlas too.
# "Wave N" sits directly under the title in both layouts, so it holds a fixed
# offset from the anchor. It crops the whole centred line, caption included.
MODAL_WAVE_REGION: Region = Region(dx=6, dy=111, w=408, h=54)

# Tier and coins do NOT hold a fixed offset. When the run beats its record the
# modal grows a green "New Highest Wave!" line between Wave and Tier, and
# everything below it moves - while the modal's top edge moves the other way,
# because a taller modal re-centres. Measured: the anchor rises 49px and these
# two lines fall 49px, so a fixed offset reads the wrong row on every record run.
#
# So they are located by their own caption, the same way navigate.py finds
# buttons. Verified against both layouts.
MODAL_TIER_CAPTION: str = "modal/tier_caption.png"
MODAL_COINS_CAPTION: str = "modal/coins_caption.png"

# From the "Tier" caption's top-left. The number trails the word at a constant
# gap, so this stays right however wide the number gets.
MODAL_TIER_REGION: Region = Region(dx=110, dy=-4, w=150, h=60)

# From the "coins earned" caption's top-left. The value is CENTRED under the
# caption, so this spans the full column rather than hugging the digits.
MODAL_COINS_REGION: Region = Region(dx=-30, dy=52, w=250, h=68)

# --- Menu navigation ------------------------------------------------------
# Everything here is located by template match and tapped at the match centre,
# never by fixed coordinates - the same rule the death modal forced on us.
NAV_TARGETS: dict[str, str] = {
    "MISSIONS": "nav/missions.png",          # top-right of the main menu
    "WORKSHOP": "nav/tab_workshop.png",      # bottom tab bar
    "CARDS": "nav/tab_cards.png",            # bottom tab bar
    "BATTLE_TAB": "nav/tab_battle.png",      # bottom tab bar - back to the menu
    "MISSIONS_RETURN": "nav/missions_return.png",  # missions has no tab; this exits
}

# First-visit popups sit between a tab and its page. Cards showed a two-step
# chain: an intro dialog with "Claim", then a full-screen "40 GEMS" reward with
# CLAIM and SKIP. Try these in order until none match, then read the page.
NAV_DISMISS: tuple[str, ...] = (
    "nav/claim.png",
    "nav/claim_reward.png",
    "nav/skip.png",
)

# --- Persistence ----------------------------------------------------------
DB_PATH: Path = Path(__file__).parent / "tower_bot.db"

# One JSON file per named strategy, plus a `.active` pointer. Committed, not
# gitignored: a strategy is a decision worth reviewing in a diff, and a fresh
# clone should start from the same defaults everyone else has.
STRATEGY_DIR: Path = Path(__file__).parent / "strategies"

# Events older than this are deleted at startup. ScanCompleted is never
# stored - it fires every 2s, roughly 43,000 near-identical rows a day - so
# what remains is state changes only, and 30 days of those stays small.
EVENT_RETENTION_DAYS: int = 30

# Which menu page is on screen. Deliberately SEPARATE from SCREEN_ANCHORS:
# ScreenState models the run lifecycle only, and classify() does
# ScreenState(winner), which would raise on a name the enum does not have.
PAGE_ANCHORS: dict[str, str] = {
    "MAIN_MENU": "screens/main_menu.png",
    "WORKSHOP": "screens/workshop.png",
    "CARDS": "screens/cards.png",
    "MISSIONS": "screens/missions.png",
}

# --- Live feed ------------------------------------------------------------
# How much history the SSE ring holds. A reconnecting browser replays from
# here using Last-Event-ID, so this is also how long a laptop can sleep
# before the feed has a hole in it.
SSE_RING_SIZE: int = 500
# How often the stream endpoint looks for new events. The scan interval is 2s,
# so a quarter of a second is already imperceptible.
SSE_POLL_SECONDS: float = 0.25
# An idle stream sends a comment this often so proxies and browsers do not
# decide the connection died.
SSE_HEARTBEAT_SECONDS: float = 15.0
# How often the MJPEG stream checks whether a new frame has been published.
# Same reasoning as SSE_POLL_SECONDS: a quarter second against a 2s scan
# interval is imperceptible, and frame_stream only actually sends when the
# frame number has moved, so a faster poll here would not deliver frames any
# sooner anyway.
FRAME_POLL_SECONDS: float = 0.25

# --- Web dashboard --------------------------------------------------------
# SECURITY: loopback only, and there is no auth. The dashboard serves
# screenshots of a live session and the full event history of this machine.
# Binding 0.0.0.0 puts both on the local network in the clear - do not change
# this without putting real authentication in front of it first.
# Since the control plane landed this server is no longer read-only: anything
# that can reach it can start and stop the bot, change what it buys, end the
# process, and create or delete strategy files under strategies/. That last
# one is a write path onto the disk, not just a knob on a running loop.
# Loopback is doing real work here, not just avoiding an open port.
WEB_HOST: str = "127.0.0.1"
WEB_PORT: int = 8765
