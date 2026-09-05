"""Module inventory state: categories, effect channels and banner evidence.

There is no recorded Modules capture in this repository at any resolution, so
this file contains no geometry, no OCR anchors and no reader. What it does
contain is the shape a reader must eventually fill, and the refusals that keep
an unread screen from becoming an inventory: `read_modules_screen` always
returns an unknown inventory, and `screen_discovery.capabilities()` names B08
as the owner of the missing capture.

Two rules carry most of the weight here.

The four primary categories are derived from the catalog's `<category>-substats`
domains rather than typed out, so a category exists exactly when the versioned
catalog says its substat family does. The catalog links no unique module to a
category, which is why a named module with no slot and no substats resolves to
`unknown` instead of to the family its name suggests.

Effect channels are part of an effect's identity. A main stat, a unique effect
scaled by rarity, a substat, and the primary and assist effects of the same
module are five separate readings of five separate things; a value recorded on
one channel is unreachable from any other, and a channel field refuses a
reading that does not belong to it. Rarity scaling exists only on the unique
effect, because that is the only channel the game scales by rarity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any, Mapping

from concepts import REGISTRY

_SUBSTAT_KIND = 'module-substat'
_SUBSTAT_DOMAIN_SUFFIX = '-substats'
_UNIQUE_KIND = 'unique-module'
_MODULE_DOMAIN = 'modules'

# concept_id -> category, for every substat the catalog names. This mapping is
# the only statement in the repository about which category a stat belongs to.
SUBSTAT_CATEGORIES: Mapping[str, str] = MappingProxyType({
    c.concept_id: c.domain[:-len(_SUBSTAT_DOMAIN_SUFFIX)] for c in REGISTRY.concepts
    if c.kind == _SUBSTAT_KIND and c.domain.endswith(_SUBSTAT_DOMAIN_SUFFIX)})
PRIMARY_CATEGORIES: tuple[str, ...] = tuple(sorted(set(SUBSTAT_CATEGORIES.values())))
UNIQUE_MODULES: frozenset[str] = frozenset(
    c.concept_id for c in REGISTRY.concepts
    if c.kind == _UNIQUE_KIND and c.domain == _MODULE_DOMAIN)

# Assist is a slot role, not a substat family: the same unique module can sit
# in a primary slot or assist from a second one. It therefore never follows
# from substat evidence, only from the slot a module was observed in.
ASSIST = 'assist'
# Two different failures. AMBIGUOUS means the evidence disagrees with itself;
# UNKNOWN means there was none. Neither may ever be replaced by a guess.
AMBIGUOUS = 'ambiguous'
UNKNOWN = 'unknown'

CHANNELS = ('main_stat', 'unique_effect', 'substat', 'primary_effect', 'assist_effect')
# `unknown` (never read), `locked` (the game refuses it), `unavailable` (not
# offered on this account), `maxed` (read, and at its cap) and `unreadable`
# (looked at, and not legible) are five different facts about one value.
VALUE_STATUSES = ('observed', 'unknown', 'locked', 'unavailable', 'maxed', 'unreadable')
_VALUED_STATUSES = ('observed', 'maxed')


def substat_category(concept_id: str) -> str | None:
    """The category this substat belongs to, or None if the catalog omits it."""
    return SUBSTAT_CATEGORIES.get(concept_id)


def resolve_module_identity(label: str) -> str | None:
    """The catalog identity for an observed module name, never a near match."""
    concept = REGISTRY.resolve(label, domain=_MODULE_DOMAIN, kind=_UNIQUE_KIND)
    return concept.concept_id if concept is not None else None


def _positive(name: str, value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f'{name} must be a nonnegative integer or unknown')


def _status(name: str, status: str) -> None:
    if status not in VALUE_STATUSES:
        raise ValueError(f'{name} must be one of {VALUE_STATUSES}')


def _counted(name: str, value: int | None, status: str) -> None:
    """A count exists only where a reading claims one; absence is not zero."""
    _positive(name, value)
    _status(f'{name} status', status)
    if value is not None and status not in _VALUED_STATUSES:
        raise ValueError(f'{name} carries a number without a reading that produced it')
    if value is None and status in _VALUED_STATUSES:
        raise ValueError(f'{name} claims a reading with no value')


@dataclass(frozen=True)
class ModuleEvidence:
    """What a reader saw, so a claimed value can be traced back to a frame."""

    observed_at: float
    confidence: float
    frame_digest: str
    rect: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.observed_at):
            raise ValueError('evidence requires a finite observation time')
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError('evidence confidence must be a probability')
        if not isinstance(self.frame_digest, str) or not self.frame_digest:
            raise ValueError('evidence requires the digest of the frame it came from')
        if self.rect is not None and (len(self.rect) != 4
                                      or any(type(v) is not int for v in self.rect)):
            raise ValueError('evidence region must be four integers or unrecorded')

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> ModuleEvidence:
        rect = raw['rect']
        return cls(raw['observed_at'], raw['confidence'], raw['frame_digest'],
                   None if rect is None else tuple(rect))


@dataclass(frozen=True)
class ModuleEffect:
    """One reading on ONE channel; the channel is part of its identity."""

    channel: str
    status: str
    concept_id: str | None = None
    raw_label: str | None = None
    value: float | None = None
    # Only the unique effect scales with rarity. Recording the rarity here,
    # and nowhere else, is what keeps rarity scaling from being read back as
    # a main stat or as a primary/assist effect.
    rarity: str | None = None
    evidence: ModuleEvidence | None = None

    def __post_init__(self) -> None:
        if self.channel not in CHANNELS:
            raise ValueError(f'effect channel must be one of {CHANNELS}')
        _status('effect', self.status)
        if self.value is not None:
            if not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
                raise ValueError('effect value must be a finite number or unknown')
            if self.status not in _VALUED_STATUSES:
                raise ValueError('an unread effect may not carry a number')
            if self.evidence is None:
                raise ValueError('a value may only be claimed with the evidence for it')
        elif self.status in _VALUED_STATUSES:
            raise ValueError(f'{self.status} claims a reading with no value')
        if self.rarity is not None and self.channel != 'unique_effect':
            raise ValueError('rarity scaling belongs to the unique effect alone')
        if self.concept_id is not None:
            if REGISTRY.by_id(self.concept_id) is None:
                raise ValueError('effect identity must come from the concept catalog')
            if self.channel == 'substat' and substat_category(self.concept_id) is None:
                raise ValueError('a substat identity must name a catalogued substat')

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> ModuleEffect:
        evidence = raw['evidence']
        return cls(raw['channel'], raw['status'], raw['concept_id'], raw['raw_label'],
                   raw['value'], raw['rarity'],
                   None if evidence is None else ModuleEvidence.from_payload(evidence))


@dataclass(frozen=True)
class CategoryResolution:
    category: str
    reason: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleReading:
    """One module as observed: its slot, identity, levels and effect channels.

    Primary and assist levels are separate fields because the game levels them
    separately; neither may stand in for the other, and an assist quantity may
    never be reported below the floor the game applies to it.
    """

    slot: str
    raw_label: str
    concept_id: str | None = None
    slot_index: int | None = None
    rarity: str = UNKNOWN
    # The rarity the module dropped at. Natural-epic provenance survives every
    # later merge, so it is recorded apart from the current rarity.
    natural_rarity: str = UNKNOWN
    stars: int | None = None
    stars_status: str = UNKNOWN
    level: int | None = None
    level_status: str = UNKNOWN
    assist_level: int | None = None
    assist_level_status: str = UNKNOWN
    assist_quantity: int | None = None
    assist_quantity_floor: int | None = None
    assist_quantity_status: str = UNKNOWN
    shards: int | None = None
    shards_status: str = UNKNOWN
    main_stat: ModuleEffect | None = None
    unique_effect: ModuleEffect | None = None
    substats: tuple[ModuleEffect, ...] = ()
    primary_effect: ModuleEffect | None = None
    assist_effect: ModuleEffect | None = None

    def __post_init__(self) -> None:
        if self.slot not in PRIMARY_CATEGORIES + (ASSIST, UNKNOWN):
            raise ValueError('a module slot is a primary category, assist, or unknown')
        if not isinstance(self.raw_label, str) or not self.raw_label:
            raise ValueError('a module reading keeps the label it was read from')
        if self.slot_index is not None and (self.slot != ASSIST or type(self.slot_index) is not int
                                            or self.slot_index < 1):
            raise ValueError('only assist slots are numbered, from one')
        for name in ('rarity', 'natural_rarity'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f'{name} is a label or {UNKNOWN}, never blank')
        _counted('stars', self.stars, self.stars_status)
        _counted('level', self.level, self.level_status)
        _counted('assist level', self.assist_level, self.assist_level_status)
        _counted('assist quantity', self.assist_quantity, self.assist_quantity_status)
        _positive('assist quantity floor', self.assist_quantity_floor)
        _counted('shards', self.shards, self.shards_status)
        if (self.assist_quantity is not None and self.assist_quantity_floor is not None
                and self.assist_quantity < self.assist_quantity_floor):
            raise ValueError('an assist quantity below its floor is a misread, not a reading')
        for channel in ('main_stat', 'unique_effect', 'primary_effect', 'assist_effect'):
            effect = getattr(self, channel)
            if effect is not None and effect.channel != channel:
                raise ValueError(f'{channel} may not hold a {effect.channel} reading')
        if any(effect.channel != 'substat' for effect in self.substats):
            raise ValueError('substats may not hold another channel\'s reading')
        if self.concept_id is None:
            # Resolved from the catalog, never invented: an unrecognised label
            # keeps no identity at all rather than the closest one.
            object.__setattr__(self, 'concept_id', resolve_module_identity(self.raw_label))
        elif self.concept_id not in UNIQUE_MODULES:
            raise ValueError('a module identity must name a catalogued module')

    def effect(self, channel: str) -> tuple[ModuleEffect, ...]:
        """Every reading on this channel and on no other."""
        if channel not in CHANNELS:
            raise ValueError(f'effect channel must be one of {CHANNELS}')
        if channel == 'substat':
            return self.substats
        effect = getattr(self, channel)
        return () if effect is None else (effect,)

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> ModuleReading:
        effects = {name: None if raw[name] is None else ModuleEffect.from_payload(raw[name])
                   for name in ('main_stat', 'unique_effect', 'primary_effect', 'assist_effect')}
        return cls(**{**raw, **effects,
                      'substats': tuple(ModuleEffect.from_payload(e) for e in raw['substats'])})


@dataclass(frozen=True)
class BannerState:
    """Banner, pity progress and the restrictions the game placed on it.

    An empty restriction tuple is not "no restrictions": `restrictions_status`
    says whether anyone looked. Pity counters exist only on an observed banner,
    so an unread banner never reports zero progress towards a guarantee.
    """

    status: str = UNKNOWN
    reason: str = 'no_recorded_layout'
    banner_id: str | None = None
    pity_counter: int | None = None
    pity_threshold: int | None = None
    restrictions: tuple[str, ...] = ()
    restrictions_status: str = UNKNOWN
    observed_at: float | None = None

    def __post_init__(self) -> None:
        _status('banner', self.status)
        _status('banner restrictions', self.restrictions_status)
        _positive('pity counter', self.pity_counter)
        _positive('pity threshold', self.pity_threshold)
        if self.status not in _VALUED_STATUSES and any(
                v is not None for v in (self.banner_id, self.pity_counter,
                                        self.pity_threshold, self.observed_at)):
            raise ValueError('an unread banner carries no identity, pity or timestamp')
        if self.restrictions and self.restrictions_status not in _VALUED_STATUSES:
            raise ValueError('restrictions may only be listed by a reading that saw them')
        if (self.pity_counter is not None and self.pity_threshold is not None
                and self.pity_counter > self.pity_threshold):
            raise ValueError('pity progress cannot exceed its own threshold')

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> BannerState:
        return cls(**{**raw, 'restrictions': tuple(raw['restrictions'])})


@dataclass(frozen=True)
class ModulesInventory:
    """Equipped and owned modules, or the reason there are none to report."""

    status: str = UNKNOWN
    reason: str = 'no_recorded_layout'
    observed_at: float | None = None
    equipped: tuple[ModuleReading, ...] = ()
    owned: tuple[ModuleReading, ...] = ()
    banner: BannerState = field(default_factory=BannerState)

    def __post_init__(self) -> None:
        _status('inventory', self.status)
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError('an inventory records why it holds what it holds')
        if self.status in _VALUED_STATUSES:
            if self.observed_at is None or not math.isfinite(self.observed_at):
                raise ValueError('an observed inventory knows when it was observed')
        elif self.observed_at is not None or self.equipped or self.owned:
            # A locked or unread Modules screen has no modules on it. Silence
            # here is the point: it must not look like an empty account.
            raise ValueError(f'a {self.status} inventory carries no modules')

    def resolved_equipped(self) -> tuple[tuple[ModuleReading, CategoryResolution], ...]:
        """Every equipped module paired with the one category it resolves to."""
        return tuple((reading, resolve_category(reading)) for reading in self.equipped)

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> ModulesInventory:
        """Rebuild a saved inventory, or refuse; a partial one is not an empty one."""
        try:
            return cls(raw['status'], raw['reason'], raw['observed_at'],
                       tuple(ModuleReading.from_payload(r) for r in raw['equipped']),
                       tuple(ModuleReading.from_payload(r) for r in raw['owned']),
                       BannerState.from_payload(raw['banner']))
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f'malformed module inventory: {exc}') from exc


def resolve_category(reading: ModuleReading) -> CategoryResolution:
    """The category of one module, from its slot and its substat families.

    Two independent sources, and they must agree. A slot names a category
    directly; catalogued substats name the family they belong to. Substats
    that span two families, or that contradict the slot, leave the module
    AMBIGUOUS, and no evidence at all leaves it UNKNOWN. The module's own
    identity is deliberately not a third source: the catalog links no unique
    module to a category, so using its name would be a guess wearing a
    catalog identity.
    """
    families = {substat_category(effect.concept_id) for effect in reading.substats
                if effect.concept_id is not None}
    families.discard(None)
    slot = reading.slot if reading.slot in PRIMARY_CATEGORIES + (ASSIST,) else None
    evidence = tuple(sorted(families)) + ((f'slot:{reading.slot}',) if slot else ())
    if len(families) > 1:
        return CategoryResolution(AMBIGUOUS, 'conflicting_substats', evidence)
    family = next(iter(families), None)
    if family is not None and slot is not None:
        if family != slot:
            return CategoryResolution(AMBIGUOUS, 'conflicting_evidence', evidence)
        return CategoryResolution(family, 'slot_and_substats', evidence)
    if family is not None:
        return CategoryResolution(family, 'substats', evidence)
    if slot is not None:
        return CategoryResolution(slot, 'slot', evidence)
    return CategoryResolution(UNKNOWN, 'no_evidence', evidence)


def unknown_inventory(reason: str) -> ModulesInventory:
    """No inventory, and the reason why - never an empty one."""
    return ModulesInventory(UNKNOWN, reason)


def read_modules_screen(screen: Any, boxes: Any, *, locale: str = 'en') -> ModulesInventory:
    """Refuse to read the Modules screen; no capture of it has been recorded.

    Kept as a function rather than left absent so the refusal is callable and
    testable, and so the day a capture lands there is one place to fill in.
    Recording that capture is B08's; acting on modules is C04's and C05's.
    """
    return unknown_inventory('no_recorded_layout')
