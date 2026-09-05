from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

import config
import ocr
import perception
import tiles

FIXTURES = Path(__file__).parent / 'fixtures'


def recorded(name: str) -> tuple[ocr.TextBox, ...]:
    return tuple(ocr.TextBox(b['text'], b['confidence'], config.Rect(*b['rect']))
                 for b in json.loads((FIXTURES / 'ocr' / f'{name}.json').read_text()))


@pytest.mark.parametrize('name,context,expected', [
    ('menu_workshop_attack', 'workshop', 'workshop.attack'),
    ('menu_workshop_defense', 'workshop', 'workshop.defense'),
    ('menu_workshop_utility', 'workshop', 'workshop.utility'),
    ('in_run_lit', 'battle', 'battle.attack'),
])
def test_recorded_supported_screens(name: str, context: str, expected: str) -> None:
    import screen_discovery
    frame = cv2.imread(str(FIXTURES / f'{name}.png'))
    result = screen_discovery.discover(frame, recorded(name), context)
    assert result.screen_id == expected
    assert result.readable


@pytest.mark.parametrize('name,screen_id', [
    ('menu_workshop_info_panel', 'workshop.info_overlay'),
    ('menu_workshop_explainer_modal', 'workshop.ultimate_explainer'),
])
def test_recorded_overlays_keep_identity_and_never_expose_purchase_rows(
    name: str, screen_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import screen_discovery
    frame = cv2.imread(str(FIXTURES / f'{name}.png'))
    boxes = recorded(name)
    assert screen_discovery.discover(frame, boxes, 'workshop') == (
        screen_discovery.ScreenDiscovery(screen_id, False, 'overlay'))
    monkeypatch.setattr(ocr, 'read', lambda _: boxes)
    result = perception.observe_frame(frame, 'workshop')
    assert result.rows == ()


@pytest.mark.parametrize('frame_name,boxes_name,context', [
    ('menu_workshop_info_panel', 'menu_workshop_info_panel', 'workshop'),
    ('menu_workshop_explainer_modal', 'menu_workshop_explainer_modal', 'workshop'),
    ('game_over_fade', 'in_run_lit', 'battle'),
    ('game_over_stats', 'in_run_lit', 'battle'),
])
def test_central_bordered_overlay_fails_closed_when_expected_heading_remains(
    frame_name: str, boxes_name: str, context: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import screen_discovery
    frame = cv2.imread(str(FIXTURES / f'{frame_name}.png'))
    boxes = tuple(b for b in recorded(boxes_name)
                  if tiles.normalise(b.text) not in (
                      'currentlevel', 'maxlevel', 'ultimateweapons', 'ok'))
    if frame_name == 'menu_workshop_explainer_modal':
        boxes += tuple(b for b in recorded('menu_workshop_attack')
                       if tiles.normalise(b.text) in ('workshop', 'attackupgrades'))

    result = screen_discovery.discover(frame, boxes, context)

    assert result == screen_discovery.ScreenDiscovery(None, False, 'overlay_geometry')
    monkeypatch.setattr(ocr, 'read', lambda _: boxes)
    assert perception.observe_frame(frame, context).rows == ()


def test_runtime_rejects_geometry_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = cv2.imread(str(FIXTURES / 'menu_workshop_attack.png'))[:2300]
    monkeypatch.setattr(ocr, 'read', lambda _: recorded('menu_workshop_attack'))
    assert perception.observe_frame(frame, 'workshop').rows == ()


def test_locale_and_context_are_explicitly_validated() -> None:
    import screen_discovery
    frame = cv2.imread(str(FIXTURES / 'menu_workshop_attack.png'))
    boxes = recorded('menu_workshop_attack')
    assert not screen_discovery.discover(frame, boxes, 'workshop', locale='de').readable
    assert not screen_discovery.discover(frame, boxes, 'battle').readable


def test_unseen_is_distinct_from_visible_unreadable() -> None:
    frame = cv2.imread(str(FIXTURES / 'menu_workshop_attack.png'))
    observation = perception.parse_frame(frame, recorded('menu_workshop_attack'), 'workshop')
    assert observation.status_for('health') == 'unseen'
    assert observation.status_for('damage') == 'available'


@pytest.mark.parametrize('marker,status', [('LOCKED', 'locked'), ('MAXED', 'maxed'),
                                           ('UNAVAILABLE', 'unavailable'), ('???', 'unreadable')])
def test_row_availability_markers(marker: str, status: str) -> None:
    frame = cv2.imread(str(FIXTURES / 'in_run_lit.png'))
    boxes = tuple(ocr.TextBox(marker if b.text == '$4' else b.text, b.confidence, b.rect)
                  for b in recorded('in_run_lit'))
    row = next(r for r in perception.parse_frame(frame, boxes, 'battle').rows
               if r.upgrade_id == 'critical_chance')
    assert row.status == status
    assert row.tap is None


def test_row_identity_follows_text_when_row_order_changes() -> None:
    frame = cv2.imread(str(FIXTURES / 'menu_workshop_attack.png'))
    original = recorded('menu_workshop_attack')
    # Simulate a changed row ordering on the recorded geometry. This proves
    # identity binding, not support for an unrecorded game unlock stage.
    boxes = tuple(ocr.TextBox('Critical' if b.text == 'Damage' else
                             'Damage' if b.text == 'Critical' and b.rect.x < 540 else
                             '' if b.text == 'Chance' else b.text, b.confidence, b.rect)
                  for b in original)
    result = perception.parse_frame(frame, boxes, 'workshop')
    damage = next(r for r in result.rows if r.upgrade_id == 'damage')
    assert damage.price == 50
    assert damage.rect.y > 680


def test_duplicate_identity_and_unknown_label_cannot_expose_taps() -> None:
    frame = cv2.imread(str(FIXTURES / 'menu_workshop_attack.png'))
    boxes = tuple(ocr.TextBox('Damage' if b.text == 'Critical' else
                             '' if b.text in ('Chance', 'Factor') else b.text, b.confidence, b.rect)
                  for b in recorded('menu_workshop_attack'))
    result = perception.parse_frame(frame, boxes, 'workshop')
    assert all(r.tap is None and r.status == 'unreadable' for r in result.rows if r.upgrade_id == 'damage')
    boxes = tuple(ocr.TextBox('Unknown Power' if b.text == 'Damage' else b.text, b.confidence, b.rect)
                  for b in recorded('menu_workshop_attack'))
    unknown = next(r for r in perception.parse_frame(frame, boxes, 'workshop').rows
                   if r.upgrade_id.startswith('discovered:'))
    assert unknown.tap is None


def test_capability_matrix_names_recorded_evidence_and_missing_scope() -> None:
    import screen_discovery
    capabilities = screen_discovery.capabilities()
    assert capabilities['resolution'] == [1080, 2400]
    assert capabilities['locale'] == 'en'
    assert capabilities['complete'] is False
    assert 'battle_history_export' in capabilities['unsupported']
    assert 'unknown_overlays' in capabilities['unsupported']
    assert capabilities['guarded_overlay_geometry'] == {
        'minimum_size': [700, 600],
        'must_cover_screen_center': True,
        'recorded_evidence': [
            'menu_workshop_info_panel', 'menu_workshop_explainer_modal',
            'game_over_fade', 'game_over_stats',
        ],
    }
    for name in capabilities['readers'].values():
        assert (FIXTURES / f'{name}.png').exists()
        assert (FIXTURES / 'ocr' / f'{name}.json').exists()


def test_new_heading_cannot_validate_an_existing_target() -> None:
    import screen_discovery
    frame = cv2.imread(str(FIXTURES / 'menu_workshop_attack.png'))
    boxes = recorded('menu_workshop_attack') + (
        ocr.TextBox('UTILITY UPGRADES', .99, config.Rect(50, 401, 450, 40)),)
    assert not screen_discovery.discover(frame, boxes, 'workshop').readable


@pytest.mark.parametrize('category,label,identity', [
    ('DEFENSE', 'Health', 'health'), ('UTILITY', 'Cash Bonus', 'cash_bonus'),
])
def test_existing_battle_categories_keep_semantic_targets(
    category: str, label: str, identity: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Compatibility regression using existing recorded geometry; these are
    # not recordings of Defense/Utility and do not claim recorded coverage.
    frame = cv2.imread(str(FIXTURES / 'in_run_lit.png'))
    boxes = tuple(ocr.TextBox(category + 'UPGRADES' if b.text == 'ATTACKUPGRADES'
                              else label if b.text == 'Damage' else b.text,
                              b.confidence, b.rect) for b in recorded('in_run_lit'))
    monkeypatch.setattr(ocr, 'read', lambda _: boxes)
    row = next(r for r in perception.observe_frame(frame, 'battle').rows if r.upgrade_id == identity)
    assert row.tap is not None
    assert row.price == 10


@pytest.mark.parametrize('name,context', [
    ('in_run_lit', 'battle'), ('menu_workshop_defense', 'workshop'),
])
def test_defence_heading_alias_preserves_runtime_health_target(
    name: str, context: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import screen_discovery
    frame = cv2.imread(str(FIXTURES / f'{name}.png'))
    boxes = tuple(ocr.TextBox('DEFENCE UPGRADES' if b.text in ('ATTACKUPGRADES', 'DEFENSEUPGRADES')
                             else 'Health' if b.text == 'Damage' else b.text,
                             b.confidence, b.rect) for b in recorded(name))
    monkeypatch.setattr(ocr, 'read', lambda _: boxes)
    discovery = screen_discovery.discover(frame, boxes, context)
    assert discovery.screen_id == f'{context}.defense'
    row = next(r for r in perception.observe_frame(frame, context).rows if r.upgrade_id == 'health')
    assert row.tap is not None
