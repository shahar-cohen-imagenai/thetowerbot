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

# --- Jitter ---------------------------------------------------------------
# Every tap is an `input tap` over ADB: no travel path, no dwell, and a
# pixel derived by fixed offset from a template match. Left alone, the bot
# taps the identical pixel every time and scans on a metronome. These three
# put variance back. Zero means off for all of them - see jitter.py.
#
# Radius, in pixels, around the computed tap point. 8 on a 1080-wide
# capture is ~0.7% of screen width; a real fingertip contact patch is
# 8-10mm, which is 60-80px at this pixel density, so this is far tighter
# than genuine human variance. The bound that matters is the other side:
# the price strip is only PRICE_REGION.h tall, and strategy.MAX_TAP_JITTER_PX
# derives its ceiling from that.
TAP_JITTER_PX: float = 8.0
# Fraction applied to the scan interval (±, symmetric) and to the two
# cooldowns (+ only - they are functional minimums, see jitter.stretch).
TIMING_JITTER: float = 0.15
# Mean pause between finding a match and sending its tap. Without it the
# tap goes out in the same breath as the scan that decided it.
TAP_DELAY_SECONDS: float = 0.12

# --- Vision ---------------------------------------------------------------
TEMPLATE_DIR: Path = Path(__file__).parent / "templates"
DEFAULT_THRESHOLD: float = 0.8

# cv2.TM_CCOEFF_NORMED normalises out mean and variance, so it is blind to
# brightness: a semi-transparent popup dimming the whole page still scores
# ~1.0 against a template cropped before the popup appeared. Guard against
# THAT by also requiring the matched region to be about as bright as the
# template. The value is a ratio of mean grey level; 0.0 disables the check.
#
# This guards overlays, not prices. It is verified only for a dimming
# overlay covering the whole screen (modal fade: 1.00 lit, 0.28 dimmed - see
# vision.brightness_ratio's docstring and test_vision.py). It does NOT detect
# a per-button "cannot afford" style: measured on menu_cards.png (x1
# affordable at 40 gems, x10 not) over the button's OWN matched template -
# CARD_BUTTONS, label and border only, not the price strip - the unaffordable
# button is DESATURATED (border saturation 45.5 vs 57.0 affordable) rather
# than dimmed - its mean grey level is actually HIGHER (59.7 vs 51.7
# affordable), the wrong direction for this ratio to catch. See
# test_the_unaffordable_card_button_is_desaturated_not_dimmed in
# tests/test_shopping_templates.py for a second measurement of the same
# finding over CARD_PRICE_REGION instead - different absolute numbers,
# because it is a different region, but the same direction. The 0.75
# default stays as-is: it still does its real job (rejecting a dimmed
# overlay on shopping rows), and digit-reading (Task 5b) is required for
# card and workshop affordability regardless.
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
#
# `menu` - the size class every workshop and card price is read at (see
# shopping.py's _buy_rows and _buy_cards) - has no entry here, and that is a
# checked fact, not an oversight: prices sit on a dark panel like the in-run
# and modal classes, and the unmodified 140 default segments them correctly
# on every committed menu fixture. No override needed unless a future
# capture shows otherwise.
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

# The coin and gem counters in the menu header bar. Identical pixels on every
# menu page, but each page's ANCHOR sits somewhere different, so the offset
# is per page rather than one shared pair. Measured on the committed
# fixtures: MAIN_MENU anchors at (336, 360), WORKSHOP at (32, 244), CARDS at
# (32, 248), against a header at absolute (85, 152) and (430, 152).
#
# The boxes are wider than today's values need. The coin counter grows from
# "78" through "1.77K" to "1.23M" without moving its left edge, so the room
# has to be on the right, and a clipped glyph fails the whole read.
HEADER_REGIONS: dict[str, tuple[Region, Region]] = {
    "MAIN_MENU": (
        Region(dx=-251, dy=-208, w=170, h=68),
        Region(dx=94, dy=-208, w=190, h=68),
    ),
    "WORKSHOP": (
        Region(dx=53, dy=-92, w=170, h=68),
        Region(dx=398, dy=-92, w=190, h=68),
    ),
    "CARDS": (
        Region(dx=53, dy=-96, w=170, h=68),
        Region(dx=398, dy=-96, w=190, h=68),
    ),
}

# --- Menu shopping ---------------------------------------------------------
# The workshop's category tabs. Cut UNSELECTED: a selected tab is brighter,
# and a template cropped lit matches only its own selected state.
WORKSHOP_TABS: dict[str, str] = {
    "ATTACK": "workshop/tab_attack.png",
    "DEFENSE": "workshop/tab_defense.png",
    "UTILITY": "workshop/tab_utility.png",
}

# Every buyable thing this account can currently SEE, and which of the two
# layouts it uses. Rows behind an unlock tile are absent on purpose: a
# template that was never cut against a real frame matches nothing or matches
# everything, and there is no third option. They get added when the unlock
# reveals them and the crop comes from a live capture.
#
# Both the "row" templates (upgrade tiles) and the "tile" templates (unlock
# tiles, despite the name matching the layout they use) are cut from their
# tile's top-left CORNER, never tight to the label text - see the "row"/"tile"
# comment on PRICE_REGIONS below for why each one specifically needs that. Do
# not re-cut the unlock templates tight to "Unlock X Upgrades": that was tried
# and produces a price offset that only reads one of the three tiles correctly.
WORKSHOP_ROWS: dict[str, tuple[str, str]] = {  # name -> (template, layout)
    "Unlock Cash Bonuses": ("workshop/unlock_cash_bonuses.png", "tile"),
    "Unlock Defense Upgrades": ("workshop/unlock_defense_upgrades.png", "tile"),
    "Unlock Range Upgrades": ("workshop/unlock_range_upgrades.png", "tile"),
    "Health": ("workshop/row_health.png", "row"),
    "Health Regen": ("workshop/row_health_regen.png", "row"),
    "Damage": ("workshop/row_damage.png", "row"),
    "Attack Speed": ("workshop/row_attack_speed.png", "row"),
    "Critical Chance": ("workshop/row_critical_chance.png", "row"),
    "Critical Factor": ("workshop/row_critical_factor.png", "row"),
}

# Shipped buy order. Taken from the community consensus (see the dashboard's
# Guide page) and cut down to rows this account can actually SEE: everything
# the guides name first - Cash/Wave, Coins/Wave, Def Abs, Def%, Thorns,
# Coins/Kill - is behind one of the three unlock tiles, which is why they
# lead. Together they cost 165 coins. Defence (Health, Health Regen) comes
# before attack for the same reason the guides give it: tier 1 is a "turtle
# build" - make the tower unhittable before making it hit harder.
#
# The crit rows ship disabled. They are here so there is a row to switch on,
# which is the same reason `enabled` exists at all - the community is
# unanimous that crit costs more and scales slower than everything above it
# this early.
#
# (name, category, enabled) triples only - the template and layout come from
# WORKSHOP_ROWS, which was measured when the crops were cut. Repeating them
# here would be a second place to keep correct, and the two could drift.
SHOPPING_ROWS: tuple[tuple[str, str, bool], ...] = (
    ("Unlock Cash Bonuses", "UTILITY", True),
    ("Unlock Defense Upgrades", "DEFENSE", True),
    ("Unlock Range Upgrades", "ATTACK", True),
    ("Health", "DEFENSE", True),
    ("Health Regen", "DEFENSE", True),
    ("Damage", "ATTACK", True),
    ("Attack Speed", "ATTACK", True),
    ("Critical Chance", "ATTACK", False),
    ("Critical Factor", "ATTACK", False),
)

# Buy buttons on the Cards page. Cropped to the "x1"/"x10" quantity label and
# border only - not the price or gem icon, which live in CARD_PRICE_REGION.
# Same reasoning as WORKSHOP_ROWS: a template baked from a number that will
# change would stop matching the moment it changes.
CARD_BUTTONS: dict[str, str] = {
    "x1": "cards/buy_x1.png",
    "x10": "cards/buy_x10.png",
}

LAYOUTS: tuple[str, ...] = ("row", "tile")

# Where a price sits relative to its own matched template. Two entries because
# the workshop has two layouts and they put the number in different places: an
# upgrade row is a half-width tile with the price right of the label, an
# unlock tile is full-width with the price centred below it. One offset cannot
# reach both, and a single averaged offset would miss both.
#
# The currency icon is INSIDE these regions deliberately. The number is
# right-aligned against the icon and grows leftward, so trimming the icon off
# the right would clip a longer price from the left. The reader is taught to
# ignore the icon instead - see the `menu` size class (Task 5b).
#
# "row" is measured from the ROW TEMPLATE'S OWN top-left, which is the tile's
# corner (see WORKSHOP_ROWS templates) - constant across all four upgrade
# rows regardless of whether the label is one line ("Damage") or two
# ("Attack Speed"), because the anchor is the corner, not the text.
#
# "tile" is measured from the UNLOCK TEMPLATE'S OWN top-left, which is ALSO
# the tile's corner, not a tight crop of the label. A tight-to-label crop was
# tried first and rejected: the three unlock labels are different lengths
# ("Unlock Cash Bonuses" vs "Unlock Defense Upgrades"), the label is CENTRED
# on the tile, and the price sits centred under the TILE, not under the
# label's own left edge - so a tight label crop's top-left slides left or
# right by up to 50px depending on the name, and one dx cannot follow it.
# Anchoring on the tile corner (a fixed x=30 offset from the page edge, same
# for all three) removes that dependency, exactly as it did for rows.
# Uniqueness among the three unlock tiles (they share a border style) comes
# from including the label text in the wider corner crop, not from cropping
# tight to it - verified below 0.82 cross-match, well under the 0.9 threshold.
#
# "row"'s width was re-measured in Task 5b: at 250 the crop's right edge lands
# inside the upgrade tile's own border, a pixel-identical sliver (columns
# 237-243 of the crop, on every one of the six row prices in the committed
# fixtures) that segments as a fourth "glyph". That sliver matches nothing -
# it scores 0.0 against every real atlas entry, nowhere near
# GLYPH_MATCH_THRESHOLD - so Atlas.match returns None for it, and because a
# read is all-or-nothing (see digits.NumberReader.read) one unmatched glyph
# fails the WHOLE price, not just that glyph. The failure is safe (a refused
# read, never a wrong number) but total, so the fix has to be geometric:
# the real content (digits + coin) ends at column 213 and the border starts
# at column 237, so 225 sits in the middle of that gap - the same convention
# this file uses elsewhere for picking the middle of a measured plateau
# rather than its edge (see DIGIT_BINARY_THRESHOLDS). Trimming the right edge
# cannot clip a longer price either: the number grows LEFTWARD against the
# icon (see above), so nothing meaningful ever lived in the trimmed space.
PRICE_REGIONS: dict[str, Region] = {
    "row": Region(dx=260, dy=130, w=225, h=55),
    "tile": Region(dx=440, dy=95, w=150, h=70),
}

# From the matched buy button's own top-left (see CARD_BUTTONS). Same
# reasoning as PRICE_REGIONS: the icon is included on purpose, and there is
# room to the left for the price to grow into.
CARD_PRICE_REGION: Region = Region(dx=110, dy=29, w=210, h=55)

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

# --- Upgrade tiles --------------------------------------------------------
# An absolute rectangle on the frame. Not Region, which is anchor-relative
# (dx, dy): a Rect says where something IS, a Region says where to look
# relative to something already found. Both ocr.py and tiles.py need this,
# which is why it lives here rather than in either of them.
class Rect(NamedTuple):
    x: int
    y: int
    w: int
    h: int


# A tile is a bright bordered rectangle: a half-width upgrade tile, or a
# full-width unlock tile. Both the workshop and the in-run panel use them.
#
# Measured with tools/tile_preview.py --all against all four committed
# fixtures (menu_workshop_attack/defense/utility.png, in_run_lit.png). Real
# tiles are a tight, consistent pair of sizes: half-width upgrade tiles are
# 503x196, the full-width unlock tile is 1020x196 - so every real tile seen
# is exactly 196 tall, regardless of page or width.
#
# Everything RETR_EXTERNAL returns alongside them is either much smaller
# (glyphs, icons: w<=108, h<=94) or falls in a gap around that 196:
#   - menu bottom nav bar: ~267-807 wide, 99-106 tall
#   - in_run_lit.png's HUD panels above the upgrade area (the fixture
#     called out as likely trouble, since its background is a live battle
#     rather than a flat menu): 519 wide, 158-160 tall - close enough in
#     WIDTH to a real tile that width alone can't reject it, but still short
#     of the 196 a real tile measures
#   - in_run_lit.png's full-width top HUD bar: 1080 wide (the screen width),
#     100 tall
# So height alone separates them without needing a crop region: the widest
# gap below a real tile's 196 is above the HUD panels' 160, and TILE_MIN_H
# sits at the middle of that gap (178), the same convention used elsewhere
# in this file (see DIGIT_BINARY_THRESHOLDS) for picking the middle of a
# measured plateau rather than its edge. TILE_MAX_H (230) leaves comfortable
# headroom above 196 without reaching any measured false candidate.
#
# TILE_MIN_W (450) and TILE_MAX_W (1050) sit just outside the measured real
# widths (503 and 1020): nothing false was measured between 503 and 1020, and
# the nearest false width above 1020 is the in_run_lit.png top HUD bar at
# 1080, so 1050 sits in that gap. Width is the weaker filter here - height is
# what actually rejects the HUD panels - but it still rejects the smaller
# glyph/icon contours and stays wide, as the bounds should: they exist to
# reject the value and price panels nested inside a tile (and now, measured,
# a couple of same-size-range HUD elements), not to pin a layout a game
# update may nudge.
#
# TILE_BINARY_THRESHOLD (100) was swept from 40 to 180 across all four
# fixtures: the detected tile count did not move at any point in that range,
# so 100 sits well inside a stable plateau rather than on an edge - a tile's
# border is bright and its surroundings are dark enough that where exactly
# the cut falls barely matters.
TILE_BINARY_THRESHOLD: int = 100
TILE_MIN_W: int = 450
TILE_MAX_W: int = 1050
TILE_MIN_H: int = 178
TILE_MAX_H: int = 230

# Where the price sits within a tile, as a fraction of tile height. A tile
# holds a stat value panel above a price panel, and both parse as numbers -
# so position is what separates "3" (Damage's level) from "30" (its price).
#
# This bounds a box TOP: tiles.py compares box.rect.y, so tops are the only
# edge that enters the predicate and the only edge worth measuring. (An
# earlier revision of this comment justified its number against stat-value
# BOTTOMS, which the code never looks at.)
#
# Measured with tools/tile_preview.py plus the recorded OCR boxes (see
# tests/fixtures/ocr/*.json) against all four committed fixtures; every real
# tile is 196px tall. The two layouts put the price at very different
# heights - a half-width upgrade tile has a separate value box well above
# its price (row tiles: price top 0.638-0.709), while a full-width unlock
# tile centres its price directly under the label with no value box at all
# (unlock tiles: price top 0.536-0.551) - so the number that bounds this
# fraction from above is the unlock tiles' low end, not the row tiles'.
#
# The feasible interval is therefore (highest NUMERIC stat-value top,
# lowest price top]:
#   - highest numeric stat-value top: 0.286, the "5" on
#     menu_workshop_defense.png. ("0.00/sec", "1.00%" and "x1.20" sit at
#     0.255-0.27 but never enter this comparison at all, since parse_number
#     already refuses them; the highest that does parse besides the "5" is
#     "3" at 0.281 and "1.00" at 0.26-0.27.)
#   - lowest price top: 0.536, Unlock Cash Bonuses on
#     menu_workshop_utility.png.
# 0.41 is the middle of that (0.286, 0.536] gap, the same
# pick-the-middle-of-the-plateau convention TILE_MIN_H and
# DIGIT_BINARY_THRESHOLDS use, and leaves ~24px of margin on each side of a
# 196px tile. The two previous guesses were both measured against the wrong
# edge: 0.55 fell ABOVE the unlock price top and would have rejected the
# unlock tiles' own price outright, and 0.51 left only ~5px before doing the
# same.
TILE_PRICE_TOP_FRACTION: float = 0.41

# --- OCR -------------------------------------------------------------------
# Boxes below this are dropped inside ocr.py and never reach a consumer.
# Measured on the committed fixtures: real content read at 0.968-1.000 and
# the one observed phantom - an 'A' on empty screen - at 0.555. This sits in
# the middle of that gap, the same pick-the-middle-of-the-plateau convention
# DIGIT_BINARY_THRESHOLD uses. Calibrated on four images; revisit against
# live data.
OCR_CONFIDENCE_FLOOR: float = 0.85
