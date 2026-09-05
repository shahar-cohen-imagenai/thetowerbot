"""Focused checks for the module inventory model (C03).

No Modules capture exists in this repository, so nothing here asserts screen
geometry. The evidence objects below are hand-built state, not measurements:
they exercise the rules the model must hold regardless of which reader
eventually supplies them.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

import numpy as np
import pytest


def evidence(now: float = 1., confidence: float = .97) -> Any:
    from modules import ModuleEvidence
    return ModuleEvidence(now, confidence, 'digest', (10, 20, 30, 40))


def effect(channel: str, value: float = 42., **kwargs: Any) -> Any:
    from modules import ModuleEffect
    kwargs.setdefault('evidence', evidence())
    return ModuleEffect(channel, 'observed', value=value, **kwargs)


def substat(concept_id: str, value: float = 7.) -> Any:
    return effect('substat', value, concept_id=concept_id, raw_label=concept_id)


def module(slot: str, **kwargs: Any) -> Any:
    from modules import ModuleReading
    kwargs.setdefault('raw_label', 'Astral Deliverance')
    return ModuleReading(slot, **kwargs)


# --- categories are derived from the catalog, never typed here ---------------

def test_categories_and_identities_come_from_the_catalog() -> None:
    import modules
    from concepts import REGISTRY
    expected = {c.domain.removesuffix('-substats') for c in REGISTRY.concepts
                if c.kind == 'module-substat'}
    assert set(modules.PRIMARY_CATEGORIES) == expected == {'cannon', 'armor', 'generator', 'core'}
    assert modules.PRIMARY_CATEGORIES == tuple(sorted(expected))
    assert modules.substat_category('cannon-substats.crit-chance') == 'cannon'
    assert modules.substat_category('core-substats.death-wave-damage-x') == 'core'
    assert len(modules.SUBSTAT_CATEGORIES) == 17 + 17 + 13 + 26
    # Identities are catalog lookups. An invented name resolves to nothing at
    # all rather than to the module it merely resembles.
    assert modules.resolve_module_identity('Astral Deliverance') == 'modules.astral-deliverance'
    assert modules.resolve_module_identity('BHD') == 'modules.black-hole-digestor'
    assert modules.resolve_module_identity('Astral Deliverance MK2') is None
    assert 'modules.astral-deliverance' in modules.UNIQUE_MODULES
    assert 'reference.assist-modules' not in modules.UNIQUE_MODULES


@pytest.mark.parametrize('slot,substats,category,reason', [
    ('cannon', ('cannon-substats.crit-chance', 'cannon-substats.multishot-chance'),
     'cannon', 'slot_and_substats'),
    ('unknown', ('armor-substats.defense',), 'armor', 'substats'),
    ('generator', (), 'generator', 'slot'),
    ('assist', (), 'assist', 'slot'),
])
def test_equipped_modules_resolve_to_exactly_one_category(
        slot: str, substats: tuple[str, ...], category: str, reason: str) -> None:
    import modules
    resolution = modules.resolve_category(
        module(slot, substats=tuple(substat(s) for s in substats)))
    assert (resolution.category, resolution.reason) == (category, reason)
    assert resolution.category in modules.PRIMARY_CATEGORIES + (modules.ASSIST,)


def test_full_loadout_resolves_every_equipped_module() -> None:
    import modules
    equipped = (module('cannon', substats=(substat('cannon-substats.crit-chance'),)),
                module('armor'), module('generator'), module('core'),
                module('assist', slot_index=1), module('assist', slot_index=2))
    inventory = modules.ModulesInventory('observed', 'hand_built', observed_at=1., equipped=equipped)
    resolved = [r.category for _, r in inventory.resolved_equipped()]
    assert resolved == ['cannon', 'armor', 'generator', 'core', 'assist', 'assist']
    assert all(c not in (modules.AMBIGUOUS, modules.UNKNOWN) for c in resolved)


# --- ambiguity is never resolved by guessing --------------------------------

def test_unrecognised_and_conflicting_modules_stay_ambiguous_or_unknown() -> None:
    import modules
    stranger = module('unknown', raw_label='Quantum Whatsit')
    assert stranger.concept_id is None
    unknown = modules.resolve_category(stranger)
    assert (unknown.category, unknown.reason) == (modules.UNKNOWN, 'no_evidence')
    # A catalog identity does NOT carry a category: the catalog links no unique
    # module to a substat family, so a named module with no other evidence is
    # still unknown rather than assigned to a plausible slot.
    named = module('unknown', concept_id='modules.astral-deliverance')
    assert modules.resolve_category(named).category == modules.UNKNOWN
    mixed = modules.resolve_category(module('unknown', substats=(
        substat('cannon-substats.crit-chance'), substat('armor-substats.defense'))))
    assert (mixed.category, mixed.reason) == (modules.AMBIGUOUS, 'conflicting_substats')
    crossed = modules.resolve_category(
        module('cannon', substats=(substat('armor-substats.defense'),)))
    assert (crossed.category, crossed.reason) == (modules.AMBIGUOUS, 'conflicting_evidence')
    # An unreadable substat contributes no category and cannot be counted as
    # agreement with the ones that did read.
    from modules import ModuleEffect
    blank = ModuleEffect('substat', 'unreadable', raw_label='???')
    partial = modules.resolve_category(module('unknown', substats=(blank,)))
    assert (partial.category, partial.reason) == (modules.UNKNOWN, 'no_evidence')
    with pytest.raises(ValueError):
        substat('labs.not-a-substat')


# --- the three effect channels stay separately addressable ------------------

def test_channels_never_borrow_each_others_values() -> None:
    import modules
    reading = module(
        'cannon',
        main_stat=effect('main_stat', 42., raw_label='Damage'),
        unique_effect=effect('unique_effect', 42., raw_label='Astral Deliverance', rarity='ancestral'),
        primary_effect=effect('primary_effect', 42., raw_label='Primary'),
        assist_effect=effect('assist_effect', 42., raw_label='Assist'),
        substats=(substat('cannon-substats.crit-chance', 42.),))
    for channel in modules.CHANNELS:
        found = reading.effect(channel)
        assert len(found) == 1 and found[0].channel == channel
    assert reading.effect('unique_effect')[0].rarity == 'ancestral'
    assert all(e.rarity is None for c in ('main_stat', 'primary_effect', 'assist_effect')
               for e in reading.effect(c))
    # Rarity scaling belongs to the unique effect alone; a main stat may not
    # carry one, and no field may hold another channel's reading.
    with pytest.raises(ValueError):
        effect('main_stat', 42., rarity='ancestral')
    with pytest.raises(ValueError):
        module('cannon', main_stat=effect('unique_effect', 42.))
    with pytest.raises(ValueError):
        module('cannon', substats=(effect('main_stat', 42.),))
    with pytest.raises(ValueError):
        effect('not_a_channel', 1.)


def test_primary_and_assist_levels_and_quantity_stay_independent() -> None:
    import modules
    reading = module('assist', slot_index=1, level=120, level_status='observed',
                     assist_level=7, assist_level_status='observed',
                     assist_quantity=3, assist_quantity_floor=2, assist_quantity_status='observed',
                     stars=4, stars_status='observed', rarity='mythic', natural_rarity='epic',
                     shards=180, shards_status='observed')
    assert (reading.level, reading.assist_level) == (120, 7)
    assert (reading.assist_quantity, reading.assist_quantity_floor) == (3, 2)
    assert reading.natural_rarity == 'epic' and reading.rarity == 'mythic'
    # An assist quantity may never be reported below the floor the game applies.
    with pytest.raises(ValueError):
        module('assist', assist_quantity=1, assist_quantity_floor=2,
               assist_quantity_status='observed')
    # Every quantity is a level, not a value read off another module's channel.
    with pytest.raises(ValueError):
        module('cannon', level=-1, level_status='observed')


# --- unknown, locked, unavailable, maxed and unreadable stay apart ----------

def test_missing_observations_never_become_numbers() -> None:
    import modules
    from modules import BannerState, ModuleEffect, ModulesInventory
    assert len(set(modules.VALUE_STATUSES)) == len(modules.VALUE_STATUSES)
    assert set(modules.VALUE_STATUSES) == {'observed', 'unknown', 'locked', 'unavailable',
                                           'maxed', 'unreadable'}
    for status in ('unknown', 'locked', 'unavailable', 'unreadable'):
        blank = ModuleEffect('main_stat', status, raw_label='Damage')
        assert blank.value is None and blank.status == status
        with pytest.raises(ValueError):
            ModuleEffect('main_stat', status, value=0., evidence=evidence())
    # `maxed` is a real reading with a value; `observed` may not be claimed
    # without the evidence that produced it.
    assert ModuleEffect('main_stat', 'maxed', value=9., evidence=evidence()).value == 9.
    with pytest.raises(ValueError):
        ModuleEffect('main_stat', 'observed', value=9.)
    locked = ModulesInventory('locked', 'modules_not_unlocked')
    unknown = ModulesInventory('unknown', 'no_recorded_layout')
    assert locked != unknown and locked.equipped == () == unknown.equipped
    assert locked.status == 'locked' and unknown.status == 'unknown'
    # A banner with nothing observed is not a banner with no restrictions and
    # no pity progress.
    banner = BannerState()
    assert (banner.status, banner.pity_counter, banner.restrictions_status) == (
        'unknown', None, 'unknown')
    assert banner.restrictions == ()
    with pytest.raises(ValueError):
        BannerState('unknown', pity_counter=0)
    with pytest.raises(ValueError):
        BannerState('unknown', restrictions=('tournament_only',))
    observed = BannerState('observed', banner_id='banner-1', pity_counter=0, pity_threshold=40,
                           restrictions=('tournament_only',), restrictions_status='observed',
                           observed_at=1.)
    assert observed.pity_counter == 0 and observed.pity_threshold == 40


def test_no_recorded_layout_means_no_reader_and_no_action() -> None:
    import modules
    import screen_discovery
    frame = np.zeros((2400, 1080, 3), dtype=np.uint8)
    refusal = modules.read_modules_screen(frame, ())
    assert (refusal.status, refusal.reason) == ('unknown', 'no_recorded_layout')
    assert refusal.equipped == () and refusal.owned == () and refusal.observed_at is None
    # screen_discovery must not claim a Modules screen either, and the gap has
    # a named owner rather than a silent absence.
    assert screen_discovery.discover(frame, (), 'modules').screen_id is None
    assert screen_discovery.discover(frame, (), 'modules').reason == 'unsupported_context'
    capabilities = screen_discovery.capabilities()
    assert capabilities['unsupported_owners']['modules_screen_layout'] == 'B08'
    assert 'modules_screen_layout' in capabilities['unsupported']
    assert not any(reader.startswith('modules') for reader in capabilities['readers'])


# --- persistence across a fresh repository ----------------------------------

def observed_inventory() -> Any:
    import modules
    equipped = (module('cannon', concept_id='modules.astral-deliverance',
                       level=100, level_status='observed', rarity='ancestral',
                       natural_rarity='epic', stars=3, stars_status='observed',
                       main_stat=effect('main_stat', 42., raw_label='Damage'),
                       unique_effect=effect('unique_effect', 7., raw_label='Astral Deliverance',
                                            rarity='ancestral'),
                       substats=(substat('cannon-substats.crit-chance'),)),
                module('assist', slot_index=1, concept_id='modules.sharp-fortitude',
                       assist_level=9, assist_level_status='observed',
                       assist_effect=effect('assist_effect', 3., raw_label='Assist')))
    return modules.ModulesInventory(
        'observed', 'hand_built', observed_at=5., equipped=equipped,
        banner=modules.BannerState('observed', banner_id='banner-1', pity_counter=12,
                                   pity_threshold=40, restrictions=('event_only',),
                                   restrictions_status='observed', observed_at=5.))


def test_inventory_survives_a_fresh_repository(tmp_path: Path) -> None:
    import modules
    from account_state import AccountRepository, AccountState
    path = tmp_path / 'state.db'
    state = AccountState(AccountRepository(path))
    inventory = observed_inventory()
    state.observe_modules(inventory)
    assert state.snapshot()['revision']['modules']['status'] == 'observed'
    restored = AccountState(AccountRepository(path))
    assert restored.snapshot()['revision'] == state.snapshot()['revision']
    reloaded = AccountRepository(path).latest().modules
    assert reloaded == inventory
    categories = [r.category for _, r in reloaded.resolved_equipped()]
    assert categories == ['cannon', 'assist']
    assert reloaded.equipped[0].effect('unique_effect')[0].rarity == 'ancestral'
    assert reloaded.equipped[0].effect('main_stat')[0].value == 42.
    # An unchanged observation writes no second revision.
    state.observe_modules(observed_inventory())
    assert sqlite3.connect(path).execute(
        'select count(*) from account_revisions').fetchone()[0] == 1


def test_unknown_readings_never_overwrite_and_failed_writes_retry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import modules
    from account_state import AccountRepository, AccountState
    path = tmp_path / 'state.db'
    repo = AccountRepository(path)
    state = AccountState(repo)
    # The no-action failure: nothing could be read, so nothing is written.
    state.observe_modules(modules.read_modules_screen(None, ()))
    assert state.snapshot()['revision'] is None
    original = repo.save_account

    def fail(*args: Any) -> None:
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(repo, 'save_account', fail)
    state.observe_modules(observed_inventory())
    assert state.snapshot()['revision'] is None
    assert 'locked' in state.snapshot()['error']
    monkeypatch.setattr(repo, 'save_account', original)
    state.observe_modules(observed_inventory())
    assert state.snapshot()['revision']['modules']['status'] == 'observed'
    assert state.snapshot()['error'] is None
    # A later unknown reading leaves the saved inventory standing.
    state.observe_modules(modules.read_modules_screen(None, ()))
    assert state.snapshot()['revision']['modules']['status'] == 'observed'
    assert AccountRepository(path).latest().modules == observed_inventory()


def test_malformed_saved_inventory_is_an_error_not_a_guess(tmp_path: Path) -> None:
    from account_state import AccountRepository, AccountState
    path = tmp_path / 'state.db'
    state = AccountState(AccountRepository(path))
    state.observe_modules(observed_inventory())
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("UPDATE account_revisions SET detail = ?",
                     ('{"modules": {"status": "observed"}}',))
    conn.close()
    reopened = AccountState(AccountRepository(path))
    assert reopened.snapshot()['revision'] is None
    assert reopened.snapshot()['errors']['account']
