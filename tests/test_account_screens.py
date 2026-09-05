"""Recorded English v29.0.1 account panels, native 1080×2400.

Stats PNG/OCR captured September 5, 2026, 08:46:51 UTC (summary) and
08:51:55 UTC (tiers), reviewed before inclusion. PNG SHA-256 values:
summary cdcc34514b3e6a9d42d22049e294883f5667e2f079f5741f6119f7567cb29385;
tiers 25ddf0b0f27c5ee2f9e57d8170d3a04a3f5a9d1efad5f272f4fd4523e26ce44e.
JSON retains OCR confidence/native bounds; missing zeros are not reconstructed.
Settings fixture contains only safe SETTINGS/Stats/version OCR tokens. Its image
and other OCR are excluded because they contain an account identifier. Aggregate
history and tier rows establish neither upgrade levels nor unlocks. Runtime frame
digests hash decoded BGR pixels, distinct from these PNG file digests.
"""

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

import config
import ocr
import screens
from account_state import AccountState, AccountRepository
from strategy import Shopping

FIXTURES = Path(__file__).parent / 'fixtures' / 'account_screens'


def recorded(name: str) -> tuple[ocr.TextBox, ...]:
    return tuple(ocr.TextBox(b['text'], b['confidence'], config.Rect(*b['rect']))
                 for b in json.loads((FIXTURES / f'{name}.json').read_text()))


def frame(name: str = 'stats_summary') -> Any:
    return cv2.imread(str(FIXTURES / f'{name}.png'))


def parse(name: str, boxes: tuple[ocr.TextBox, ...] | None = None, **kwargs: Any) -> Any:
    import account_screens
    return account_screens.parse_frame(frame(name), recorded(name) if boxes is None else boxes,
                                       now=123., **kwargs)


def test_summary_preserves_raw_unknown_and_missing_values() -> None:
    result = parse('stats_summary')
    assert result.screen_id == 'account.stats.summary'
    fields = {f.key: f for f in result.fields}
    assert fields['coins_earned'].raw_value == '1.86K'
    assert fields['workshop_upgrades'].raw_value == '7'
    assert fields['cells_earned_per_hour'].status == 'insufficient_data'
    assert fields['cells_earned_per_hour'].raw_value == 'Need More Data'
    assert fields['interest_earned'].status == 'unreadable'
    assert fields['interest_earned'].raw_value is None
    assert result.frame_width == 1080 and result.frame_height == 2400
    assert len(result.frame_digest) == 64
    assert not result.tiers
    assert parse('stats_summary', tuple(reversed(recorded('stats_summary')))) == result


def test_tiers_match_each_column_without_filling_missing_cells() -> None:
    result = parse('stats_tiers')
    assert result.screen_id == 'account.stats.tiers'
    assert [r.tier for r in result.tiers] == list(range(1, 25))
    assert [result.tiers[0].wave.raw_value, result.tiers[0].coins.raw_value,
            result.tiers[0].cells.raw_value] == ['10', '43', '0']
    assert result.tiers[1].cells.status == 'unreadable'  # OCR confidence below .90
    assert result.tiers[3].wave.raw_value is None
    assert parse('stats_tiers', tuple(reversed(recorded('stats_tiers')))) == result


def test_duplicate_value_and_low_confidence_are_unreadable() -> None:
    boxes = recorded('stats_summary')
    value = next(b for b in boxes if b.text == '1.86K')
    for duplicate, altered in (
        (True, boxes + (value,)),
        (False, tuple(replace(b, confidence=.89) if b == value else b for b in boxes)),
    ):
        fields = {f.key: f for f in parse('stats_summary', altered).fields}
        assert fields['coins_earned'].status == 'unreadable'
        assert fields['coins_earned'].raw_value == (None if duplicate else '1.86K')
        assert fields['coins_earned'].confidence == (0. if duplicate else .89)
        assert (fields['coins_earned'].rect is None) == duplicate


@pytest.mark.parametrize('name,heading', [('stats_summary', 'STATS'), ('stats_tiers', 'Coins')])
def test_ambiguous_headings_rejected(name: str, heading: str) -> None:
    boxes = recorded(name)
    assert parse(name, boxes + (next(b for b in boxes if b.text == heading),)) is None


def test_geometry_locale_and_title_required() -> None:
    import account_screens
    assert parse('stats_summary', locale='fr') is None
    assert account_screens.parse_frame(np.zeros((1200, 540, 3), dtype=np.uint8),
                                      recorded('stats_summary'), now=123.) is None
    assert parse('stats_summary', tuple(b for b in recorded('stats_summary') if b.text != 'STATS')) is None


def test_settings_exposes_only_safe_version() -> None:
    import account_screens
    boxes = recorded('settings_safe') + (ocr.TextBox('PRIVATE-ACCOUNT-IDENTIFIER', .99, config.Rect(300, 1400, 300, 30)),)
    result = account_screens.parse_frame(frame(), boxes, now=123.)
    assert result.screen_id == 'account.settings'
    assert [(f.key, f.raw_value) for f in result.fields] == [('game_version', 'v29.0.1')]
    assert 'PRIVATE' not in str(result)


def test_readings_never_promote_account_facts_or_persist(tmp_path: Path) -> None:
    state = AccountState(AccountRepository(tmp_path / 'account.db'))
    state.screen_readings.observe(parse('stats_summary'))
    state.screen_readings.observe(parse('stats_tiers'))
    snapshot = state.snapshot()
    assert snapshot['revision'] is None
    assert snapshot['unknown_state']['workshop_levels'] is None
    assert len(snapshot['screen_readings']['readings']) == 2
    state.screen_readings.observe(None)
    assert state.snapshot()['screen_readings']['current_screen_id'] is None
    assert len(state.snapshot()['screen_readings']['readings']) == 2
    assert AccountState(AccountRepository(tmp_path / 'account.db')).snapshot()['screen_readings']['readings'] == []


@pytest.mark.parametrize('paused', [True, False])
@pytest.mark.parametrize('active_visit', [True, False])
def test_runtime_reads_without_any_action_even_when_main_menu_matches(
    bot_on_main_menu: Any, monkeypatch: pytest.MonkeyPatch, paused: bool, active_visit: bool,
) -> None:
    from shopping import Step
    bot = bot_on_main_menu(Shopping(enabled=True))
    bot.account_state = AccountState()
    bot._screen = frame()
    bot.controls.paused = paused
    if active_visit:
        bot.shopping._step = Step.RETURN
    monkeypatch.setattr(screens, 'classify', lambda *_: screens.ScreenReading(screens.ScreenState.MAIN_MENU, 1., {'MAIN_MENU': 1.}))
    monkeypatch.setattr(ocr, 'read', lambda *args, **kwargs: recorded('stats_summary'))
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail('account modal reached an action path')
    monkeypatch.setattr(bot, '_manage_speed', forbidden)
    monkeypatch.setattr(bot.shopping, 'begin', forbidden)
    monkeypatch.setattr(bot.shopping, 'advance', forbidden)
    monkeypatch.setattr(bot.navigator, 'maybe_navigate', forbidden)
    assert not bot.run_once()
    assert not bot.device.taps
    assert bot.account_state.snapshot()['screen_readings']['current_screen_id'] == 'account.stats.summary'


def test_runtime_ocr_error_blocks_and_preserves_only_historical_readings(
    bot_on_main_menu: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = bot_on_main_menu(Shopping(enabled=True))
    bot.account_state = AccountState()
    bot.account_state.screen_readings.observe(parse('stats_summary'))
    bot._screen = frame()
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError('secret exception contents')
    monkeypatch.setattr(ocr, 'read', fail)
    assert not bot.run_once()
    result = bot.account_state.snapshot()['screen_readings']
    assert result['current_screen_id'] is None and result['error']
    assert 'secret' not in result['error']
    assert len(result['readings']) == 1 and not bot.device.taps


def test_strict_ocr_error_is_visible_without_changing_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, '_engine_or_none', lambda: None)
    assert ocr.read(frame()) == ()
    with pytest.raises(RuntimeError):
        ocr.read(frame(), strict=True)


@pytest.mark.parametrize('duplicate', [False, True])
def test_candidate_modal_blocks_even_when_no_reading_can_be_accepted(
    monkeypatch: pytest.MonkeyPatch, duplicate: bool,
) -> None:
    from account_screens import ScreenReadings
    boxes = recorded('stats_summary')
    title = next(b for b in boxes if b.text == 'STATS')
    invalid = boxes + (title,) if duplicate else tuple(replace(b, confidence=.5) if b == title else b for b in boxes)
    monkeypatch.setattr(ocr, 'read', lambda *args, **kwargs: invalid)
    state = ScreenReadings()
    assert state.scan(frame()) is True
    assert state.snapshot()['current_screen_id'] is None
    assert state.snapshot()['error']


def test_title_prefilter_can_retain_low_confidence_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    def engine(_: Any) -> Any:
        return [([[0, 0], [100, 0], [100, 40], [0, 40]], 'STATS', .5)], None
    monkeypatch.setattr(ocr, '_engine_or_none', lambda: engine)
    assert ocr.read(frame()) == ()
    assert ocr.read(frame(), strict=True, min_confidence=0.)[0].text == 'STATS'


def test_home_clears_current_but_retains_timestamped_history(monkeypatch: pytest.MonkeyPatch) -> None:
    from account_screens import ScreenReadings
    state = ScreenReadings()
    state.observe(parse('stats_summary'))
    monkeypatch.setattr(ocr, 'read', lambda *args, **kwargs: ())
    assert not state.scan(frame())
    snapshot = state.snapshot()
    assert snapshot['current_screen_id'] is None and snapshot['error'] is None
    assert snapshot['readings'][0]['observed_at'] == 123.


def test_api_exposes_readings_and_explains_guard() -> None:
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from web.app import create_app
    from sinks.state import BotState
    from sinks.sse import SseSink
    from events import EventBus
    from control import Controls
    from strategy import Strategy
    from autopilot import AutopilotState
    account = AccountState()
    account.screen_readings.observe(parse('stats_summary'))
    runner = SimpleNamespace(account_state=account, autopilot_state=AutopilotState(),
                             status=lambda: {'running': True, 'since': 123., 'error': None})
    app = create_app(state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None,
                     runner=runner, account_state=account, controls=Controls(strategy=Strategy.from_config()))
    with TestClient(app) as client:
        payload = client.get('/api/account').json()
        assert payload['revision'] is None
        assert payload['screen_readings']['current_screen_id'] == 'account.stats.summary'
        readiness = client.get('/api/status').json()['runtime']['readiness']
        assert readiness['mode'] == 'observing'
        assert any('account' in reason.lower() for reason in readiness['reasons'])


def test_duplicate_tier_row_is_rejected_and_duplicate_cell_is_unknown() -> None:
    boxes = recorded('stats_tiers')
    tier = next(b for b in boxes if b.text == 'Tier 1')
    assert parse('stats_tiers', boxes + (tier,)) is None
    cell = next(b for b in boxes if b.text == '43')
    result = parse('stats_tiers', boxes + (cell,))
    assert result.tiers[0].coins.status == 'unreadable'
    assert result.tiers[0].wave.raw_value == '10'


def test_snapshot_is_detached_and_reset_keeps_only_historical_readings() -> None:
    from account_screens import ScreenReadings
    state = ScreenReadings()
    state.observe(parse('stats_summary'))
    snapshot = state.snapshot()
    snapshot['readings'][0]['screen_id'] = 'tampered'
    state.reset_current()
    actual = state.snapshot()
    assert actual['current_screen_id'] is None
    assert actual['readings'][0]['screen_id'] == 'account.stats.summary'


@pytest.mark.parametrize('name,first_label,second_label,values', [
    ('stats_summary', 'Coins Earned', 'Cash Earned', ('1.86K',)),
    ('stats_tiers', 'Tier 1', 'Tier 2', ('10', '43')),
])
@pytest.mark.parametrize('offset', [0, 30])
def test_overlapping_row_anchors_cannot_claim_the_same_values(
    name: str, first_label: str, second_label: str, values: tuple[str, ...], offset: int,
) -> None:
    boxes = recorded(name)
    first = next(b for b in boxes if b.text == first_label)
    altered = tuple(
        replace(b, rect=b.rect._replace(y=first.rect.y + offset)) if b.text == second_label
        else replace(b, rect=b.rect._replace(y=b.rect.y + offset // 2)) if b.text in values
        else b for b in boxes)
    assert parse(name, altered) is None


@pytest.mark.parametrize('confidence', [float('inf'), 1.1])
def test_invalid_confidence_cannot_be_hidden_by_label_confidence(confidence: float) -> None:
    boxes = tuple(replace(b, confidence=confidence) if b.text == '1.86K' else b
                  for b in recorded('stats_summary'))
    field = next(f for f in parse('stats_summary', boxes).fields if f.key == 'coins_earned')
    assert field.status == 'unreadable'
    assert field.raw_value is None
