"""Ultimate Weapons state, read only as far as recorded evidence reaches.

The nine base weapons come from the versioned catalog, never from names
written here, so a catalog revision moves this module rather than silently
disagreeing with it.

One capture exists: `tests/fixtures/menu_workshop_ultimate.png`, the Workshop
ULTIMATE UPGRADES tab on an account whose UW system is still locked behind
tournaments. It shows the page title, the "New Ultimate Weapon" offer with its
price, and the prerequisite line, and it shows no owned weapon at all. So this
reader can say the system is locked and what it costs to leave that state; it
cannot say anything about an owned weapon's rows, its toggle, its cooldown or
the UW+ system. Those are `unknown` here and declared in
`screen_discovery.capabilities()` with the task that owns them.

The separation this module exists to enforce: a stone-purchased quantity and a
lab- or substat-adjusted one are different types in different collections.
Labs themselves are L01's model; all that is kept here is which lab identity
adjusted a number, so a caller can never read an adjusted value as if stones
had bought it.

Nothing in this module produces a tap. Spending power stones is U02.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from typing import Any

import ocr
import screen_discovery
import tiles
from concepts import REGISTRY, Concept
from config import Rect
from device import Image

SCREEN_ID = 'workshop.ultimate_upgrades'

_UW_DOMAIN = 'ultimate-weapons'
_UW_KIND = 'ultimate-weapon'
# The domain also carries references ('UW stone upgrades', 'UW substats') and
# strategies ('Perma (BH / GT / CF)'). Only the weapon kind is a weapon.
_BASE = tuple(sorted((c for c in REGISTRY.concepts
                      if c.domain == _UW_DOMAIN and c.kind == _UW_KIND),
                     key=lambda c: c.concept_id))
_BASE_IDS = frozenset(c.concept_id for c in _BASE)
# What may legitimately adjust a UW number. A weapon is not an adjustment of
# itself, which is the confusion this set exists to make impossible.
_ADJUSTING_IDS = frozenset(
    c.concept_id for c in REGISTRY.concepts if c.domain == 'labs'
) | {'reference.uw-substats'}

# Ownership of a weapon. `unknown` is "never observed", `locked` is "the
# account cannot own this yet", `not_owned` is "the page was read and this
# weapon was not on it". Collapsing any pair of those loses a real fact.
OWNERSHIP_STATES = frozenset({'unknown', 'locked', 'not_owned', 'owned', 'unreadable'})
# Shared by every quantity, raw or adjusted. `unavailable` and `maxed` are not
# each other and neither of them is zero.
QUANTITY_STATES = frozenset({'unknown', 'observed', 'locked', 'unavailable',
                             'maxed', 'unreadable'})
SYSTEM_STATES = frozenset({'unknown', 'locked', 'unlocked', 'unreadable'})
_OFFER_STATES = frozenset({'offered', 'ambiguous', 'unreadable'})
_ADJUSTING_SOURCES = frozenset({'labs', 'uw_substats'})
# A serialized marker, checked on the way back in. The type separation below
# is the real defence; this catches a row that was edited in the database.
_RAW_PROVENANCE = 'stone_upgrade'

# What one locked capture cannot show, and who owns each gap. Declared in the
# support matrix rather than duplicated here.
UNSUPPORTED_OWNERS = screen_discovery.ULTIMATE_WEAPON_GAPS

_MIN_CONFIDENCE = .90
_EXPECTED_FRAME = (2400, 1080)

# Measured on the capture: 'WORKSHOP' at (31, 249, 298, 40) and
# 'ULTIMATEUPGRADES' at (51, 401, 507, 40). The heading band is the same one
# screen_discovery measured for the Attack/Defense/Utility headings, which is
# why an Ultimate page and an upgrade page can never be each other.
_TITLE = Rect(0, 220, 620, 80)
_HEADING = Rect(0, 370, 700, 90)
_HEADING_LABEL = 'ultimateupgrades'
# The single bordered offer card, 'New Ultimate Weapon' at (326, 522, 427, 45)
# with its price at (512, 589, 35, 40). Bounded to that one card: this reader
# has never seen a page with two.
_OFFER_BAND = Rect(0, 470, 1080, 210)
_OFFER_LABEL = 'newultimateweapon'
_PRICE = re.compile(r'\d+')
# 'Unlock tournaments by reaching wave 60' at (49, 1059, 980, 53). Matched on
# the raw text: tiles.normalise would fuse the number into the sentence.
_PREREQUISITE_BAND = Rect(0, 960, 1080, 200)
_WAVE_PREREQUISITE = re.compile(
    r'unlock\s+tournaments?\s+by\s+reaching\s+wave\s+(\d+)\.?', re.I)


def base_identities() -> tuple[Concept, ...]:
    """The base Ultimate Weapons the catalog names, in stable id order."""
    return _BASE


def _checked(value: str, allowed: frozenset[str], what: str) -> str:
    if value not in allowed:
        raise ValueError(f'{what} must be one of {sorted(allowed)}, not {value!r}')
    return value


def _number(value: float | int | None, what: str) -> float | int | None:
    """A missing measurement stays None. It never becomes zero."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{what} must be a number or unknown')
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'{what} must be finite and nonnegative')
    return value


@dataclass(frozen=True)
class StoneUpgrade:
    """A raw quantity power stones bought on the weapon's own page.

    The catalog names the weapon, not its individual stone rows, so one raw
    quantity per weapon is exactly as much as this repository can name.
    Distinguishing the rows means adding catalog entries, which changes what
    the bot may buy and belongs to the catalog task.
    """

    concept_id: str
    level: int | None
    raw_value: float | None
    cost: float | None
    status: str
    # Never varies. Present so a row read back out of the database that claims
    # any other origin is refused instead of trusted.
    provenance: str = _RAW_PROVENANCE

    def __post_init__(self) -> None:
        if self.concept_id not in _BASE_IDS:
            raise ValueError(f'{self.concept_id!r} is not a base Ultimate Weapon')
        if self.provenance != _RAW_PROVENANCE:
            raise ValueError('a stone upgrade cannot carry another provenance')
        _checked(self.status, QUANTITY_STATES, 'status')
        if self.level is not None and (isinstance(self.level, bool)
                                       or not isinstance(self.level, int) or self.level < 0):
            raise ValueError('level must be a nonnegative integer or unknown')
        _number(self.raw_value, 'raw_value')
        _number(self.cost, 'cost')


@dataclass(frozen=True)
class AdjustedValue:
    """A UW quantity after labs or UW substats have moved it.

    This is not a stone upgrade and cannot be stored as one. `contributors`
    names the lab or substat identities behind the number so its provenance
    travels with it; modelling those identities is L01's work, not this
    module's.
    """

    concept_id: str
    aspect: str
    value: float | None
    status: str
    source: str
    contributors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.concept_id not in _BASE_IDS:
            raise ValueError(f'{self.concept_id!r} is not a base Ultimate Weapon')
        if not isinstance(self.aspect, str) or not self.aspect.strip():
            raise ValueError('an adjusted value must name the aspect it describes')
        _checked(self.status, QUANTITY_STATES, 'status')
        _checked(self.source, _ADJUSTING_SOURCES, 'source')
        if not self.contributors:
            raise ValueError('an adjusted value must name what adjusted it')
        unknown = [c for c in self.contributors if c not in _ADJUSTING_IDS]
        if unknown:
            raise ValueError(f'{unknown[0]!r} does not adjust an Ultimate Weapon')
        _number(self.value, 'value')


@dataclass(frozen=True)
class UltimateWeaponState:
    """One of the nine, with its raw and adjusted quantities kept apart.

    `stone_upgrades` and `adjusted_values` are None when nothing was observed.
    An empty tuple would read as "this weapon has none", which is a different
    and much stronger claim than "nobody has looked".
    """

    concept_id: str
    name: str
    ownership: str
    stone_upgrades: tuple[StoneUpgrade, ...] | None = None
    adjusted_values: tuple[AdjustedValue, ...] | None = None
    plus_status: str = 'unknown'
    toggle_status: str = 'unknown'

    def __post_init__(self) -> None:
        if self.concept_id not in _BASE_IDS:
            raise ValueError(f'{self.concept_id!r} is not a base Ultimate Weapon')
        _checked(self.ownership, OWNERSHIP_STATES, 'ownership')
        _checked(self.plus_status, QUANTITY_STATES, 'plus_status')
        _checked(self.toggle_status, QUANTITY_STATES, 'toggle_status')
        # A TypeError rather than a ValueError, because putting one of these
        # in the other's collection is a category error and not a bad value.
        for entries, expected in ((self.stone_upgrades, StoneUpgrade),
                                  (self.adjusted_values, AdjustedValue)):
            if entries is None:
                continue
            if not isinstance(entries, tuple):
                raise TypeError('quantities must be held in a tuple')
            for entry in entries:
                if type(entry) is not expected:
                    raise TypeError(f'{type(entry).__name__} cannot be stored as '
                                    f'{expected.__name__}')
                if entry.concept_id != self.concept_id:
                    raise ValueError('a quantity must belong to the weapon holding it')
        if self.stone_upgrades is not None and len(
                {u.concept_id for u in self.stone_upgrades}) != len(self.stone_upgrades):
            raise ValueError('duplicate raw identities are ambiguous')
        if self.adjusted_values is not None and len(
                {v.aspect for v in self.adjusted_values}) != len(self.adjusted_values):
            raise ValueError('duplicate adjusted aspects are ambiguous')

    def raw_stone_upgrade(self, concept_id: str) -> StoneUpgrade | None:
        """The stone-purchased quantity for this identity, or None.

        Deliberately blind to `adjusted_values`: whatever it is asked for, a
        caller reaching for a raw number cannot be handed an adjusted one.
        """
        return next((u for u in self.stone_upgrades or () if u.concept_id == concept_id), None)

    def adjusted_value(self, aspect: str) -> AdjustedValue | None:
        """The lab/substat-adjusted quantity for this aspect, or None."""
        return next((v for v in self.adjusted_values or () if v.aspect == aspect), None)


@dataclass(frozen=True)
class Requirement:
    """An account prerequisite the page states in words.

    `met` is None because this reader sees the sentence, not the account's
    wave record. An unchecked prerequisite is not a failed one.
    """

    raw_text: str
    kind: str
    threshold: int | None
    met: bool | None = None


@dataclass(frozen=True)
class UnlockOffer:
    """An unlock choice the page offers, with the evidence that read it.

    `rect` is where the label was seen, kept so a later task can justify a
    target. It is not a target: this module never produces one.
    """

    label: str
    cost: int | None
    cost_currency: str | None
    status: str
    confidence: float
    rect: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        _checked(self.status, _OFFER_STATES, 'status')


@dataclass(frozen=True)
class UltimateWeaponsRecord:
    """Everything known about the UW system at one moment, all nine included."""

    registry_version: str
    observed_at: float | None
    screen_id: str | None
    system_status: str
    frame_width: int | None = None
    frame_height: int | None = None
    frame_digest: str | None = None
    prerequisite: Requirement | None = None
    offers: tuple[UnlockOffer, ...] = ()
    weapons: tuple[UltimateWeaponState, ...] = ()
    unsupported: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _checked(self.system_status, SYSTEM_STATES, 'system_status')
        if tuple(w.concept_id for w in self.weapons) != tuple(c.concept_id for c in _BASE):
            raise ValueError('a record must represent every base Ultimate Weapon once')

    def weapon(self, concept_id: str) -> UltimateWeaponState | None:
        return next((w for w in self.weapons if w.concept_id == concept_id), None)


def unknown_record(*, ownership: str = 'unknown') -> UltimateWeaponsRecord:
    """A record that claims nothing, with all nine identities still present."""
    return UltimateWeaponsRecord(
        REGISTRY.registry_version, None, None,
        'locked' if ownership == 'locked' else 'unknown',
        weapons=tuple(UltimateWeaponState(c.concept_id, c.name, ownership) for c in _BASE),
        unsupported=tuple(sorted(UNSUPPORTED_OWNERS)))


def _trusted(box: ocr.TextBox) -> bool:
    return math.isfinite(box.confidence) and _MIN_CONFIDENCE <= box.confidence <= 1.


def _inside(box: ocr.TextBox, rect: Rect) -> bool:
    b = box.rect
    return (b.w > 0 and b.h > 0 and rect.x <= b.x and rect.y <= b.y
            and b.x + b.w <= rect.x + rect.w and b.y + b.h <= rect.y + rect.h)


def _single(boxes: tuple[ocr.TextBox, ...], label: str, rect: Rect) -> ocr.TextBox | None:
    """The one trusted box with this label in a measured band, or None.

    Two candidates are refused exactly like none: a duplicated anchor is not
    evidence that the page is what it looks like.
    """
    matches = [b for b in boxes if _inside(b, rect)
               and tiles.normalise(b.text) == label and _trusted(b)]
    return matches[0] if len(matches) == 1 else None


def _prerequisite(boxes: tuple[ocr.TextBox, ...]) -> Requirement | None:
    matches = [(b, m) for b, m in
               ((b, _WAVE_PREREQUISITE.fullmatch(b.text.strip())) for b in boxes
                if _trusted(b) and _inside(b, _PREREQUISITE_BAND)) if m]
    if len(matches) != 1:
        return None
    box, match = matches[0]
    return Requirement(box.text.strip(), 'wave', int(match[1]), None)


def _offers(boxes: tuple[ocr.TextBox, ...]) -> tuple[UnlockOffer, ...]:
    """The unlock choices in the measured offer card, priced only if certain."""
    labels = [b for b in boxes if _inside(b, _OFFER_BAND)
              and tiles.normalise(b.text) == _OFFER_LABEL]
    if not labels:
        return ()
    if len(labels) > 1 or not _trusted(labels[0]):
        # Two cards, or one that could not be read. Either way there is no
        # single offer to describe and no price to attach to it.
        status = 'ambiguous' if len(labels) > 1 else 'unreadable'
        return (UnlockOffer('New Ultimate Weapon', None, None, status, 0., None),)
    label = labels[0]
    prices = [b for b in boxes if _inside(b, _OFFER_BAND) and b is not label
              and _PRICE.fullmatch(b.text.strip())]
    rect = (label.rect.x, label.rect.y, label.rect.w, label.rect.h)
    if len(prices) != 1 or not _trusted(prices[0]):
        return (UnlockOffer(label.text.strip(), None, None, 'ambiguous',
                            label.confidence, rect),)
    # The glyph beside the price is an icon, not text. The page names power
    # stones in a sentence lower down; reading a price's currency out of a
    # sentence elsewhere on the page would be inference, not measurement.
    return (UnlockOffer(label.text.strip(), int(prices[0].text.strip()), None,
                        'offered', min(label.confidence, prices[0].confidence), rect),)


def read_page(screen: Image, boxes: tuple[ocr.TextBox, ...], *,
              now: float | None = None, locale: str = 'en') -> UltimateWeaponsRecord | None:
    """Read the bare ULTIMATE UPGRADES page, or return None having read nothing.

    None means this reader reached no conclusion about the frame - the wrong
    geometry, another page, or an overlay covering this one. It never means
    the page was seen and found empty.
    """
    observed_at = time.time() if now is None else now
    if screen.shape[:2] != _EXPECTED_FRAME or locale != 'en' or not math.isfinite(observed_at):
        return None
    # Overlay and page identity are one judgement, made once, in
    # screen_discovery. The bare Ultimate page is the frame that reader
    # examines, finds no upgrade heading on, and refuses; anything else it
    # says - an overlay, an upgrade tab, unusable geometry - is not this page.
    discovery = screen_discovery.discover(screen, boxes, 'workshop', locale=locale)
    if discovery.screen_id is not None or discovery.reason != 'ambiguous_or_unreadable_heading':
        return None
    if _single(boxes, 'workshop', _TITLE) is None:
        return None
    heading = _single(boxes, _HEADING_LABEL, _HEADING)
    if heading is None or not 0 <= heading.rect.x <= 70:
        return None
    prerequisite = _prerequisite(boxes)
    # The prerequisite banner is the page telling us the system is shut. With
    # no banner this is a stage no capture covers, so the reader says it does
    # not know rather than inferring that the system is open.
    system_status = 'locked' if prerequisite is not None else 'unknown'
    ownership = 'locked' if system_status == 'locked' else 'unknown'
    height, width = screen.shape[:2]
    return UltimateWeaponsRecord(
        REGISTRY.registry_version, observed_at, SCREEN_ID, system_status,
        width, height, hashlib.sha256(screen.tobytes()).hexdigest(),
        prerequisite, _offers(boxes),
        tuple(UltimateWeaponState(c.concept_id, c.name, ownership) for c in _BASE),
        tuple(sorted(UNSUPPORTED_OWNERS)))


def _rebuilt(rows: Any, kind: type) -> tuple[Any, ...] | None:
    """Rebuild one stored collection, or None when nothing was ever stored."""
    if rows is None:
        return None
    if not isinstance(rows, (list, tuple)):
        raise ValueError('stored quantities must be a list')
    rebuilt = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('a stored quantity must be an object')
        fields = dict(row)
        if 'contributors' in fields:
            fields['contributors'] = tuple(fields['contributors'] or ())
        rebuilt.append(kind(**fields))
    return tuple(rebuilt)


def decode(payload: dict[str, Any]) -> UltimateWeaponsRecord:
    """Rebuild a stored record, revalidating it rather than trusting the row.

    Every constructor above runs again here, so a row edited in the database
    to move a lab-adjusted number into a weapon's raw collection is refused on
    the way out and not merely on the way in.
    """
    try:
        fields = dict(payload)
        prerequisite = fields.get('prerequisite')
        fields['prerequisite'] = Requirement(**prerequisite) if prerequisite else None
        fields['offers'] = tuple(UnlockOffer(**o) for o in fields.get('offers') or ())
        fields['unsupported'] = tuple(fields.get('unsupported') or ())
        weapons = []
        for row in fields.get('weapons') or ():
            weapon = dict(row)
            weapon['stone_upgrades'] = _rebuilt(weapon.get('stone_upgrades'), StoneUpgrade)
            weapon['adjusted_values'] = _rebuilt(weapon.get('adjusted_values'), AdjustedValue)
            weapons.append(UltimateWeaponState(**weapon))
        fields['weapons'] = tuple(weapons)
        return UltimateWeaponsRecord(**fields)
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValueError(f'malformed Ultimate Weapons record: {exc}') from exc
