"""Evidence-bounded readers for recorded English upgrade screens.

The recorded 1080x2400 Workshop grids, battle Attack grid and Daily Missions
page provide geometry evidence. Existing battle Defense/Utility readers remain
supported, but lack recorded coverage. This is not every game screen or unlock
stage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import ocr
import tiles
from device import Image


_RECORDED_READERS = (
    ('workshop.attack', 'menu_workshop_attack'),
    ('workshop.defense', 'menu_workshop_defense'),
    ('workshop.utility', 'menu_workshop_utility'),
    ('battle.attack', 'in_run_lit'),
    ('battle.defense', 'in_run_defense'),
    ('battle.utility', 'in_run_utility'),
    ('missions.daily', 'menu_missions'),
)

# Screens read at two genuinely different points in an account's life: the
# rows on the second capture are not the rows on the first, which is what
# makes the screen id a property of the page rather than of one save file.
#
# Only the Workshop tabs qualify, and the bar is deliberately this high.
# in_run_early parses to the same four rows, prices and targets as in_run_lit;
# stats_summary_early and stats_tiers_early parse identically to their
# originals; menu_missions_weekly differs from menu_missions by a daily
# rotation, which is not an unlock. Those captures are still evidence - see
# _RECORDED_READERS, where two of them give battle.defense and battle.utility
# their first recorded layout - but they are not stage evidence, and listing
# them here would make this table mean nothing.
_RECORDED_UNLOCK_STAGES = (
    ('workshop.attack', ('menu_workshop_attack', 'menu_workshop_attack_early')),
    ('workshop.defense', ('menu_workshop_defense', 'menu_workshop_defense_early')),
    ('workshop.utility', ('menu_workshop_utility', 'menu_workshop_utility_early')),
)

# Rows the game shows and the catalog cannot name. perception already refuses
# them a tap and reports them as `discovered:<label>`; listing them turns that
# silent refusal into visible, ownable work. Extending the catalog is a
# purchasing change and belongs to the catalog task, not to screen discovery.
_UNCATALOGUED_LABELS = (
    ('attackrange', 'menu_workshop_attack_early'),
    ('unlockmultishotupgrades', 'menu_workshop_attack_early'),
    ('unlockthornupgrades', 'menu_workshop_defense_early'),
)

# The Ultimate Upgrades page IS recorded, but only at its locked stage: the
# capture is an account whose UW system is still shut behind tournaments, so
# it carries an unlock offer and a prerequisite and not one owned weapon.
# Everything only an owned weapon could show is therefore still unread.
# ultimate_weapons.py reads this table instead of keeping its own copy, so a
# reader and the matrix describing it cannot drift apart.
ULTIMATE_WEAPON_GAPS = {
    'ultimate_weapon_owned_page_layout': 'B08',
    'ultimate_weapon_stone_upgrade_rows': 'U02',
    'ultimate_weapon_toggles_and_cooldowns': 'U03',
    'uw_plus_system_state': 'U04',
}

# What remains out of scope, and who owns it. An entry with no owner is a
# standing property of the design rather than work someone will pick up.
_UNSUPPORTED_OWNERS = {
    'later_unlock_stage_layouts_outside_workshop': 'B08',
    'battle_history_export': 'B08',
    'native_stat_export': 'B08',
    'missions_claim_actions': 'T01',
    'missions_reward_currency': 'T01',
    'missions_milestone_claim_state': 'T01',
    'missions_beyond_the_recorded_strip': 'T01',
    'other_locales': 'V06',
    'other_resolutions': 'V06',
    'unknown_overlays': 'by_design',
    **ULTIMATE_WEAPON_GAPS,
}

# Three independent anchors measured on the recorded Daily Missions capture:
# the page title at (32, 249, 433, 40), the WEEKLY CHALLENGE banner at
# (276, 378, 527, 40) and the "N/M Missions" band at (27, 653, 287, 43). One
# anchor could be a coincidence; the page is claimed only when all three land
# where they were measured, and never when an upgrade heading is also present.
_MISSIONS_TITLE_Y = (230, 270)
_MISSIONS_BANNER_Y = (358, 418)
_MISSIONS_COUNT_Y = (633, 693)
# Matched against the raw text, not tiles.normalise: normalising strips the
# slash, and "2/8 Missions" would become indistinguishable from "28 missions".
_MISSIONS_COUNT = re.compile(r'(\d+)\s*/\s*(\d+)\s*missions', re.I)

# These bounds cover the central bordered panels in the two recorded Workshop
# overlays (740x702 and 890x1132) and two game-over captures (986x1116/1212).
# Recorded upgrade tiles are only 196px high. This is a fail-closed guard for
# that measured geometry, not recognition of every overlay the game can show.
_GUARDED_OVERLAY_MIN_W = 700
_GUARDED_OVERLAY_MIN_H = 600
_GUARDED_OVERLAY_EVIDENCE = (
    'menu_workshop_info_panel',
    'menu_workshop_explainer_modal',
    'game_over_fade',
    'game_over_stats',
)


def _has_guarded_overlay(screen: Image) -> bool:
    """Return whether a measured large panel covers the screen centre."""
    height, width = screen.shape[:2]
    centre_x, centre_y = width // 2, height // 2
    return any(
        rect.w >= _GUARDED_OVERLAY_MIN_W
        and rect.h >= _GUARDED_OVERLAY_MIN_H
        and rect.x <= centre_x < rect.x + rect.w
        and rect.y <= centre_y < rect.y + rect.h
        for rect in tiles.candidates(screen)
    )


# The two measured y bands an upgrade heading occupies: the Workshop page's
# and the in-run panel's. Shared, so that recognising a heading and refusing
# one are the same judgement made in one place.
_WORKSHOP_HEADING_Y = (380, 420)
_BATTLE_HEADING_Y = (1640, 1680)
_UPGRADE_HEADINGS = ('attackupgrades', 'defenseupgrades', 'utilityupgrades')


def _upgrade_label(box: ocr.TextBox) -> str | None:
    """The upgrade category this box names, or None if it names none.

    An exact label, never a suffix. A mission card reading "Buy 20 battle
    upgrades" ends in the same word and is prose, not a heading.
    """
    label = tiles.normalise(box.text).replace('defence', 'defense')
    return label.removesuffix('upgrades') if label in _UPGRADE_HEADINGS else None


def _is_upgrade_heading(box: ocr.TextBox) -> bool:
    """Whether this box is an upgrade heading WHERE an upgrade heading sits.

    Position is part of the identity. Without it this is a word match, and
    any text anywhere on any screen can borrow a heading's meaning.
    """
    return (_upgrade_label(box) is not None and box.confidence >= .9
            and 0 <= box.rect.x <= 70
            and (_WORKSHOP_HEADING_Y[0] <= box.rect.y <= _WORKSHOP_HEADING_Y[1]
                 or _BATTLE_HEADING_Y[0] <= box.rect.y <= _BATTLE_HEADING_Y[1]))


def _single(boxes: tuple[ocr.TextBox, ...], label: str,
            bounds: tuple[int, int]) -> ocr.TextBox | None:
    """The one trusted box with this label inside a measured y band, or None.

    Two candidates are ambiguous and yield None exactly like none at all: a
    duplicated anchor is not evidence that the page is what it looks like.
    """
    top, bottom = bounds
    matches = [b for b in boxes if tiles.normalise(b.text) == label
               and b.confidence >= .9 and top <= b.rect.y <= bottom]
    return matches[0] if len(matches) == 1 else None


def missions_count(boxes: tuple[ocr.TextBox, ...]) -> tuple[int, int] | None:
    """The "N/M Missions" band as (shown, offered), or None if not certain."""
    matches = [m for m in (_MISSIONS_COUNT.fullmatch(b.text.strip())
                           for b in boxes if b.confidence >= .9
                           and _MISSIONS_COUNT_Y[0] <= b.rect.y <= _MISSIONS_COUNT_Y[1]) if m]
    if len(matches) != 1:
        return None
    shown, offered = int(matches[0][1]), int(matches[0][2])
    return (shown, offered) if shown <= offered else None


def _discover_missions(boxes: tuple[ocr.TextBox, ...]) -> ScreenDiscovery:
    """Claim the Daily Missions page only on all three measured anchors."""
    # An upgrade page must never be read as missions. Keyed on a heading at
    # its measured place, because the missions page is FULL of prose that
    # mentions upgrades: "Buy 20 battle upgrades" is an ordinary daily, and
    # matching it here made the reader fail on any day it was drawn.
    if any(_is_upgrade_heading(b) for b in boxes):
        return ScreenDiscovery(None, False, 'unsupported_layout')
    title = _single(boxes, 'dailymissions', _MISSIONS_TITLE_Y)
    banner = _single(boxes, 'weeklychallenge', _MISSIONS_BANNER_Y)
    if title is None or banner is None or missions_count(boxes) is None:
        return ScreenDiscovery(None, False, 'ambiguous_or_unreadable_heading')
    if not 0 <= title.rect.x <= 70:
        return ScreenDiscovery(None, False, 'unsupported_layout')
    return ScreenDiscovery('missions.daily', True, 'recorded_layout')


def capabilities() -> dict[str, Any]:
    """Return a detached support matrix; catalog existence is not coverage."""
    return {
        'schema_version': 1,
        'complete': False,
        'resolution': [1080, 2400],
        'locale': 'en',
        'readers': dict(_RECORDED_READERS),
        'recorded_verified': [reader for reader, _ in _RECORDED_READERS],
        'existing_runtime_supported': [],
        'recorded_unlock_stages': {screen: list(names)
                                   for screen, names in _RECORDED_UNLOCK_STAGES},
        'uncatalogued_labels': dict(_UNCATALOGUED_LABELS),
        'account_screens': ['account.settings', 'account.stats.summary',
                            'account.stats.tiers'],
        'recognized_overlays': {
            'workshop.info_overlay': 'menu_workshop_info_panel',
            'workshop.ultimate_explainer': 'menu_workshop_explainer_modal',
        },
        'guarded_overlay_geometry': {
            'minimum_size': [_GUARDED_OVERLAY_MIN_W, _GUARDED_OVERLAY_MIN_H],
            'must_cover_screen_center': True,
            'recorded_evidence': list(_GUARDED_OVERLAY_EVIDENCE),
        },
        'unsupported_owners': dict(_UNSUPPORTED_OWNERS),
        # Recorded, and honest about which stage was recorded. Absent from
        # 'readers' deliberately: discover() still refuses this page, because
        # it is not a screen the purchase loop may ever be handed rows from.
        'ultimate_weapons': {
            'recorded_stage': 'menu_workshop_ultimate',
            'recorded_stage_meaning': 'locked_system',
            'reader': 'ultimate_weapons.read_page',
            'base_identities': 9,
            'unread': sorted(ULTIMATE_WEAPON_GAPS),
        },
        'unsupported': [
            # 'nested_account_menus' was removed once the Settings -> Stats
            # menu was read and proven. 'later_unlock_stage_layouts' narrowed
            # to the screens that still have no second-stage capture rather
            # than disappearing: the Workshop tabs have one, nothing else does.
            'later_unlock_stage_layouts_outside_workshop',
            'battle_history_export', 'native_stat_export',
            'other_locales', 'other_resolutions', 'unknown_overlays',
            # A claimable capture and the full 5..35 strip are now recorded
            # (menu_missions_claimable and menu_missions_weekly), so the
            # limits below are no longer about missing evidence. They are
            # about the reader: a card whose progress bar is replaced by a
            # CLAIM button carries no "N / M" text, so it parses as
            # `unreadable` with no mission id at all. The reader sees that a
            # card is there and honestly reports it cannot identify it. It
            # observes; it never claims a reward or names a currency.
            'missions_claim_actions', 'missions_reward_currency',
            'missions_milestone_claim_state', 'missions_beyond_the_recorded_strip',
            # Not "no capture exists" for the page - one does - but "no
            # capture exists of an account that owns a UW", which is what
            # every entry below would have to be read from.
            *sorted(ULTIMATE_WEAPON_GAPS),
        ],
        # Declared readers whose behaviour is consistent with the recorded
        # evidence but NOT demonstrated by it. Kept apart from 'unsupported'
        # deliberately: these paths do run, and the risk is a reader being
        # trusted for a property no capture has actually shown.
        'unproven': {
            'missions.identity_across_reordering':
                'The recorded page holds two missions in one ordering. The '
                'focused checks translate those boxes between the two card '
                'rectangles, which shows the reader does not key off row '
                'index but is not a second recorded ordering. A multi-mission '
                'capture of the same set reordered would settle it.',
            'missions.identity_across_ocr_jitter':
                'The one real mission text on the capture reads as '
                '"Kill zo0basic enemies" at confidence .9208, above the .90 '
                'gate. A misread cannot inherit a clean mission id, but a '
                'stable id for that mission is unproven on a single sample.',
        },
    }


@dataclass(frozen=True)
class ScreenDiscovery:
    screen_id: str | None
    readable: bool
    reason: str


def discover(
    screen: Image, boxes: tuple[ocr.TextBox, ...], context: str, *, locale: str = 'en'
) -> ScreenDiscovery:
    """Validate context and measured heading geometry before exposing rows.

    Locale is a configured assertion, additionally checked against the known
    English headings. It is not automatic detection of an entire game locale.
    Unknown screens and recorded overlays never provide action coordinates.
    """
    if screen.shape[:2] != (2400, 1080):
        return ScreenDiscovery(None, False, 'unsupported_geometry')
    if locale != 'en':
        return ScreenDiscovery(None, False, 'unsupported_locale')
    if context not in ('workshop', 'battle', 'missions'):
        return ScreenDiscovery(None, False, 'unsupported_context')
    labels = {tiles.normalise(b.text) for b in boxes}
    if {'currentlevel', 'maxlevel'} <= labels:
        return ScreenDiscovery('workshop.info_overlay', False, 'overlay')
    if {'ultimateweapons', 'ok'} <= labels:
        return ScreenDiscovery('workshop.ultimate_explainer', False, 'overlay')
    if _has_guarded_overlay(screen):
        return ScreenDiscovery(None, False, 'overlay_geometry')
    if context == 'missions':
        return _discover_missions(boxes)
    headings = [b for b in boxes if _upgrade_label(b) is not None]
    if len(headings) != 1 or headings[0].confidence < .9:
        return ScreenDiscovery(None, False, 'ambiguous_or_unreadable_heading')
    heading = headings[0]
    category = _upgrade_label(heading)
    if context == 'workshop':
        titles = [b for b in boxes if tiles.normalise(b.text) == 'workshop']
        valid = (len(titles) == 1 and titles[0].confidence >= .9
                 and 230 <= titles[0].rect.y <= 270
                 and _WORKSHOP_HEADING_Y[0] <= heading.rect.y <= _WORKSHOP_HEADING_Y[1])
    else:
        valid = ('workshop' not in labels
                 and _BATTLE_HEADING_Y[0] <= heading.rect.y <= _BATTLE_HEADING_Y[1])
    if not valid or not (0 <= heading.rect.x <= 70):
        return ScreenDiscovery(None, False, 'unsupported_layout')
    screen_id = f'{context}.{category}'
    reason = 'recorded_layout' if screen_id in dict(_RECORDED_READERS) else 'existing_runtime_supported'
    return ScreenDiscovery(screen_id, True, reason)
