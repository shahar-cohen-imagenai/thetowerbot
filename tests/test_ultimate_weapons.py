"""Focused checks for U01: reading Ultimate Weapons without inventing them.

The only recorded Ultimate Upgrades capture is `menu_workshop_ultimate.png`,
taken on an account whose UW system is still locked behind tournaments. It
proves the page's identity, its unlock offer and its prerequisite, and it
proves nothing at all about an owned weapon's rows. Everything below is
either bound to that capture or is a contract check on the state model.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import cv2
import numpy as np
import pytest

import config
import ocr

FIXTURES = Path(__file__).parent / 'fixtures'

GOLDEN_TOWER = 'ultimate-weapons.golden_tower'
GOLDEN_TOWER_DURATION_LAB = 'labs.golden-tower-duration'


def recorded(name: str) -> tuple[ocr.TextBox, ...]:
    return tuple(ocr.TextBox(b['text'], b['confidence'], config.Rect(*b['rect']))
                 for b in json.loads((FIXTURES / 'ocr' / f'{name}.json').read_text()))


def frame(name: str) -> np.ndarray:
    image = cv2.imread(str(FIXTURES / f'{name}.png'))
    assert image is not None, f'missing fixture: {name}.png'
    return image


def locked_reading():
    import ultimate_weapons
    record = ultimate_weapons.read_page(frame('menu_workshop_ultimate'),
                                        recorded('menu_workshop_ultimate'), now=100.)
    assert record is not None
    return record


def raw_and_adjusted():
    """One raw stone upgrade and one lab-adjusted value for the same weapon."""
    import ultimate_weapons
    raw = ultimate_weapons.StoneUpgrade(GOLDEN_TOWER, level=3, raw_value=12.,
                                        cost=5., status='observed')
    adjusted = ultimate_weapons.AdjustedValue(
        GOLDEN_TOWER, aspect='duration', value=17., status='observed',
        source='labs', contributors=(GOLDEN_TOWER_DURATION_LAB,))
    return raw, adjusted


def test_the_catalog_yields_exactly_the_nine_base_identities() -> None:
    """Identity comes from the versioned catalog, never from a literal here."""
    import ultimate_weapons
    from concepts import REGISTRY
    identities = ultimate_weapons.base_identities()
    assert len(identities) == 9
    assert len({c.concept_id for c in identities}) == 9
    assert all(c.domain == 'ultimate-weapons' and c.kind == 'ultimate-weapon'
               for c in identities)
    # The domain also holds references and strategies. A reference is not a
    # weapon: 'UW stone upgrades' and 'Perma (BH / GT / CF)' must never be
    # counted as one of the nine.
    domain = [c for c in REGISTRY.concepts if c.domain == 'ultimate-weapons']
    assert len(domain) > 9
    assert all(c.kind == 'ultimate-weapon' for c in identities)
    assert 'reference.uw' not in {c.concept_id for c in identities}
    # Sorted, so two readings of the same account compare directly.
    assert [c.concept_id for c in identities] == sorted(c.concept_id for c in identities)


def test_every_reading_represents_all_nine_identities() -> None:
    """Ownership is a fact about each of the nine, not about the rows drawn."""
    import ultimate_weapons
    expected = tuple(c.concept_id for c in ultimate_weapons.base_identities())
    for record in (locked_reading(), ultimate_weapons.unknown_record()):
        assert tuple(w.concept_id for w in record.weapons) == expected


def test_the_recorded_page_reads_as_locked_and_never_as_zero() -> None:
    """The capture's whole content: one offer, one prerequisite, no weapons."""
    import ultimate_weapons
    record = locked_reading()
    assert record.screen_id == ultimate_weapons.SCREEN_ID
    assert record.system_status == 'locked'
    assert record.prerequisite is not None
    assert record.prerequisite.kind == 'wave'
    assert record.prerequisite.threshold == 60
    # This reader cannot see the account's wave record, so whether the
    # prerequisite is met is unknown rather than False.
    assert record.prerequisite.met is None
    assert len(record.offers) == 1
    offer = record.offers[0]
    assert offer.status == 'offered'
    assert offer.cost == 5
    # The glyph beside the price is an icon, not text. The page names power
    # stones in a sentence elsewhere; that is prose, not this price's label.
    assert offer.cost_currency is None
    for weapon in record.weapons:
        assert weapon.ownership == 'locked'
        # Never (): an empty tuple would read as "this weapon has no stone
        # upgrades", and the capture shows no upgrade rows whatsoever.
        assert weapon.stone_upgrades is None
        assert weapon.adjusted_values is None
        assert weapon.plus_status == 'unknown'
        assert weapon.toggle_status == 'unknown'


def test_an_unobserved_account_is_unknown_and_not_confused_with_locked() -> None:
    import ultimate_weapons
    unknown = ultimate_weapons.unknown_record()
    assert unknown.system_status == 'unknown'
    assert unknown.screen_id is None
    assert unknown.prerequisite is None
    assert unknown.offers == ()
    assert {w.ownership for w in unknown.weapons} == {'unknown'}
    assert {w.ownership for w in locked_reading().weapons} == {'locked'}
    # The five game states stay five distinct words.
    assert {'unknown', 'locked', 'unavailable', 'maxed', 'unreadable'} <= (
        ultimate_weapons.QUANTITY_STATES)
    assert len(ultimate_weapons.OWNERSHIP_STATES) == 5


@pytest.mark.parametrize('name', [
    # A different Workshop tab is not this page.
    'menu_workshop_attack',
    # The same page under its explainer overlay. Nothing behind an overlay is
    # evidence, however familiar the words on it look.
    'menu_workshop_explainer_modal',
])
def test_a_frame_that_is_not_the_bare_page_yields_no_reading(name: str) -> None:
    import ultimate_weapons
    assert ultimate_weapons.read_page(frame(name), recorded(name), now=100.) is None


def test_an_unmeasured_frame_is_not_looked_at_rather_than_found_empty() -> None:
    import ultimate_weapons
    boxes = recorded('menu_workshop_ultimate')
    small = np.zeros((1200, 540, 3), dtype=np.uint8)
    assert ultimate_weapons.read_page(small, boxes, now=100.) is None
    assert ultimate_weapons.read_page(frame('menu_workshop_ultimate'), boxes,
                                      now=100., locale='de') is None


def test_a_duplicated_price_is_ambiguous_and_never_becomes_a_number() -> None:
    """Two candidates for one cost are not evidence for either."""
    import ultimate_weapons
    boxes = recorded('menu_workshop_ultimate')
    doubled = boxes + (ocr.TextBox('7', .99, config.Rect(600, 589, 35, 40)),)
    record = ultimate_weapons.read_page(frame('menu_workshop_ultimate'), doubled, now=100.)
    assert record is not None
    assert record.system_status == 'locked'
    assert len(record.offers) == 1
    assert record.offers[0].status == 'ambiguous'
    assert record.offers[0].cost is None


def test_a_duplicated_heading_refuses_the_page_outright() -> None:
    import ultimate_weapons
    boxes = recorded('menu_workshop_ultimate')
    doubled = boxes + (ocr.TextBox('ULTIMATEUPGRADES', .99, config.Rect(51, 410, 507, 40)),)
    assert ultimate_weapons.read_page(frame('menu_workshop_ultimate'), doubled, now=100.) is None


def test_the_reader_offers_no_action() -> None:
    """U01 observes. Spending power stones belongs to U02."""
    import ultimate_weapons
    record = locked_reading()
    assert not hasattr(record.offers[0], 'tap')
    assert not any(name.startswith(('buy', 'tap', 'purchase', 'unlock_'))
                   for name in dir(ultimate_weapons))


def test_an_adjusted_value_cannot_masquerade_as_a_raw_stone_upgrade() -> None:
    """The sharp half of the acceptance gate.

    A lab- or substat-adjusted number and a stone-purchased one are separate
    types held in separately addressed collections, so a caller cannot reach
    for one and be handed the other.
    """
    import ultimate_weapons
    raw, adjusted = raw_and_adjusted()
    state = ultimate_weapons.UltimateWeaponState(
        GOLDEN_TOWER, 'Golden Tower', 'owned',
        stone_upgrades=(raw,), adjusted_values=(adjusted,))
    assert state.raw_stone_upgrade(GOLDEN_TOWER) is raw
    assert state.adjusted_value('duration') is adjusted
    # The raw accessor knows nothing of the adjusted collection, whatever it
    # is asked for.
    assert state.raw_stone_upgrade(GOLDEN_TOWER_DURATION_LAB) is None
    assert state.raw_stone_upgrade('duration') is None
    assert raw.provenance == 'stone_upgrade'

    # An adjusted value may not be placed in the raw collection, nor vice versa.
    with pytest.raises(TypeError):
        ultimate_weapons.UltimateWeaponState(GOLDEN_TOWER, 'Golden Tower', 'owned',
                                             stone_upgrades=(adjusted,))
    with pytest.raises(TypeError):
        ultimate_weapons.UltimateWeaponState(GOLDEN_TOWER, 'Golden Tower', 'owned',
                                             adjusted_values=(raw,))
    # A lab identity is not something stones buy on a UW page.
    with pytest.raises(ValueError):
        ultimate_weapons.StoneUpgrade(GOLDEN_TOWER_DURATION_LAB, level=1,
                                      raw_value=1., cost=1., status='observed')
    # An adjusted value must name what adjusted it, and the weapon itself is
    # not an adjustment of the weapon.
    with pytest.raises(ValueError):
        ultimate_weapons.AdjustedValue(GOLDEN_TOWER, aspect='duration', value=17.,
                                       status='observed', source='labs',
                                       contributors=(GOLDEN_TOWER,))
    with pytest.raises(ValueError):
        ultimate_weapons.AdjustedValue(GOLDEN_TOWER, aspect='duration', value=17.,
                                       status='observed', source='labs',
                                       contributors=())
    # A status outside the shared vocabulary is not a sixth state.
    with pytest.raises(ValueError):
        ultimate_weapons.StoneUpgrade(GOLDEN_TOWER, level=None, raw_value=None,
                                      cost=None, status='probably')


def test_a_stored_adjusted_row_is_refused_when_it_is_read_back() -> None:
    """The separation has to survive the database, not just the constructor."""
    import ultimate_weapons
    raw, adjusted = raw_and_adjusted()
    record = _record_with(raw, adjusted)
    payload = asdict(record)
    weapon = next(w for w in payload['weapons'] if w['concept_id'] == GOLDEN_TOWER)

    tampered = json.loads(json.dumps(payload))
    row = next(w for w in tampered['weapons'] if w['concept_id'] == GOLDEN_TOWER)
    row['stone_upgrades'].append(asdict(adjusted))
    with pytest.raises(ValueError):
        ultimate_weapons.decode(tampered)

    # The subtler forgery: a row shaped exactly like a stone upgrade whose
    # provenance says it came from labs.
    disguised = json.loads(json.dumps(payload))
    row = next(w for w in disguised['weapons'] if w['concept_id'] == GOLDEN_TOWER)
    row['stone_upgrades'].append({'concept_id': GOLDEN_TOWER, 'level': None,
                                  'raw_value': 17., 'cost': None,
                                  'status': 'observed', 'provenance': 'labs'})
    with pytest.raises(ValueError):
        ultimate_weapons.decode(disguised)

    # The untampered payload still holds exactly the one raw row, and it
    # rebuilds into the record it came from.
    assert weapon['stone_upgrades'] == (asdict(raw),)
    assert ultimate_weapons.decode(json.loads(json.dumps(payload))) == record


def _record_with(raw, adjusted):
    import ultimate_weapons
    record = ultimate_weapons.unknown_record()
    weapons = tuple(replace(w, ownership='owned', stone_upgrades=(raw,),
                            adjusted_values=(adjusted,))
                    if w.concept_id == GOLDEN_TOWER else w
                    for w in record.weapons)
    return replace(record, observed_at=100., weapons=weapons)


def test_a_reading_survives_a_fresh_repository_over_the_same_file(tmp_path: Path) -> None:
    """Restart proof: a new repository and state over the same sqlite path."""
    import ultimate_weapons
    from account_state import AccountRepository, AccountRevision, AccountState
    path = tmp_path / 'state.db'
    raw, adjusted = raw_and_adjusted()
    record = _record_with(raw, adjusted)
    AccountRepository(path).save_account(
        AccountRevision(created_at=100., ultimate_weapons=record), ())

    restarted = AccountState(AccountRepository(path))
    revision = restarted.repository.latest()
    assert revision is not None
    assert revision.ultimate_weapons == record
    weapon = next(w for w in revision.ultimate_weapons.weapons
                  if w.concept_id == GOLDEN_TOWER)
    assert weapon.raw_stone_upgrade(GOLDEN_TOWER) == raw
    assert weapon.adjusted_value('duration') == adjusted
    assert weapon.raw_stone_upgrade(GOLDEN_TOWER_DURATION_LAB) is None

    # The API payload keeps the two collections apart as well, so nothing
    # reading it can merge them either.
    snapshot = restarted.snapshot()['revision']['ultimate_weapons']
    stored = next(w for w in snapshot['weapons'] if w['concept_id'] == GOLDEN_TOWER)
    assert stored['stone_upgrades'] == json.loads(json.dumps([asdict(raw)]))
    assert stored['adjusted_values'] == json.loads(json.dumps([asdict(adjusted)]))
    assert stored['stone_upgrades'][0]['provenance'] == 'stone_upgrade'


def test_an_account_with_no_uw_reading_keeps_the_field_unknown(tmp_path: Path) -> None:
    """A missing section stays missing; it never becomes an empty reading."""
    from account_state import AccountRepository, AccountRevision, AccountState
    path = tmp_path / 'state.db'
    AccountRepository(path).save_account(AccountRevision(created_at=1.), ())
    assert AccountRepository(path).latest().ultimate_weapons is None
    assert AccountState(AccountRepository(path)).snapshot()['revision']['ultimate_weapons'] is None


def test_the_support_matrix_names_who_owns_the_geometry_u01_cannot_read() -> None:
    """What one locked capture cannot show is declared, not guessed at."""
    import screen_discovery
    capabilities = screen_discovery.capabilities()
    owners = capabilities['unsupported_owners']
    unsupported = capabilities['unsupported']
    for entry in ('ultimate_weapon_owned_page_layout',
                  'ultimate_weapon_stone_upgrade_rows',
                  'ultimate_weapon_toggles_and_cooldowns',
                  'uw_plus_system_state'):
        assert entry in unsupported
        assert owners[entry]
    matrix = capabilities['ultimate_weapons']
    assert matrix['recorded_stage'] == 'menu_workshop_ultimate'
    assert matrix['recorded_stage_meaning'] == 'locked_system'
    assert matrix['base_identities'] == 9
    # The page is still not a claimable purchase screen: B04's refusal holds.
    result = screen_discovery.discover(frame('menu_workshop_ultimate'),
                                       recorded('menu_workshop_ultimate'), 'workshop')
    assert result.screen_id is None and not result.readable
