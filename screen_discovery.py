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
    ('missions.daily', 'menu_missions'),
)

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
    if any(tiles.normalise(b.text).replace('defence', 'defense').endswith('upgrades')
           for b in boxes):
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
        'existing_runtime_supported': ['battle.defense', 'battle.utility'],
        'recognized_overlays': {
            'workshop.info_overlay': 'menu_workshop_info_panel',
            'workshop.ultimate_explainer': 'menu_workshop_explainer_modal',
        },
        'guarded_overlay_geometry': {
            'minimum_size': [_GUARDED_OVERLAY_MIN_W, _GUARDED_OVERLAY_MIN_H],
            'must_cover_screen_center': True,
            'recorded_evidence': list(_GUARDED_OVERLAY_EVIDENCE),
        },
        'unsupported': [
            'later_unlock_stage_layouts',
            'nested_account_menus', 'battle_history_export', 'native_stat_export',
            'other_locales', 'other_resolutions', 'unknown_overlays',
            # The recorded Missions page is a zero-progress capture: nothing on
            # it is claimable, its weekly strip runs off the right edge, and
            # its reward icons were never separated from one another. This
            # reader observes; it never claims a reward or names a currency.
            'missions_claim_actions', 'missions_reward_currency',
            'missions_milestone_claim_state', 'missions_beyond_the_recorded_strip',
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
            'missions.shown_of_offered':
                'That the "N/M Missions" band counts drawn-of-offered rather '
                'than finished-of-offered is inferred from the separate '
                'completed counter, not stated by the page.',
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
    headings = [b for b in boxes if tiles.normalise(b.text).replace('defence', 'defense') in (
        'attackupgrades', 'defenseupgrades', 'utilityupgrades')]
    if len(headings) != 1 or headings[0].confidence < .9:
        return ScreenDiscovery(None, False, 'ambiguous_or_unreadable_heading')
    heading = headings[0]
    category = tiles.normalise(heading.text).replace('defence', 'defense').removesuffix('upgrades')
    if context == 'workshop':
        titles = [b for b in boxes if tiles.normalise(b.text) == 'workshop']
        valid = (len(titles) == 1 and titles[0].confidence >= .9
                 and 230 <= titles[0].rect.y <= 270
                 and 380 <= heading.rect.y <= 420)
    else:
        valid = ('workshop' not in labels
                 and 1640 <= heading.rect.y <= 1680)
    if not valid or not (0 <= heading.rect.x <= 70):
        return ScreenDiscovery(None, False, 'unsupported_layout')
    screen_id = f'{context}.{category}'
    reason = 'recorded_layout' if screen_id in dict(_RECORDED_READERS) else 'existing_runtime_supported'
    return ScreenDiscovery(screen_id, True, reason)
