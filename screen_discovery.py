"""Evidence-bounded readers for recorded English upgrade screens.

The recorded 1080x2400 Workshop grids and battle Attack grid provide
geometry evidence. Existing battle Defense/Utility readers remain supported,
but lack recorded coverage. This is not every game screen or unlock stage.
"""
from __future__ import annotations

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
)

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
        ],
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
    if context not in ('workshop', 'battle'):
        return ScreenDiscovery(None, False, 'unsupported_context')
    labels = {tiles.normalise(b.text) for b in boxes}
    if {'currentlevel', 'maxlevel'} <= labels:
        return ScreenDiscovery('workshop.info_overlay', False, 'overlay')
    if {'ultimateweapons', 'ok'} <= labels:
        return ScreenDiscovery('workshop.ultimate_explainer', False, 'overlay')
    if _has_guarded_overlay(screen):
        return ScreenDiscovery(None, False, 'overlay_geometry')
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
