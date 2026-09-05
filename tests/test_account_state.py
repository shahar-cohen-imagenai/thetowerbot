from dataclasses import replace
from pathlib import Path
from typing import Any
import sqlite3

import pytest
import config
from perception import Observation, ObservedUpgrade


def reading(value: float | None = 100., context: str = 'workshop',
            confidence: float = .99, now: float = 1.) -> Observation:
    row = ObservedUpgrade('health', 'Health', 'DEFENSE', context, value, 10,
                          'available', now, config.Rect(1, 2, 3, 4), (2, 3),
                          confidence=confidence, raw_name='Health', raw_value=str(value))
    return Observation('DEFENSE', (row,), {}, None, now, 1,
                       context=context, frame_digest='abc', frame_width=100, frame_height=200)


@pytest.mark.parametrize('battle_first', [True, False])
def test_verifies_only_workshop_and_reopens(tmp_path: Path, battle_first: bool) -> None:
    from account_state import AccountRepository, AccountState
    path = tmp_path / 'state.db'
    state = AccountState(AccountRepository(path))
    battle = reading(900., 'battle')
    if battle_first:
        state.observe_run(battle, 7)
    state.observe_account(reading())
    assert state.snapshot()['revision'] is None
    state.observe_account(reading(now=2.))
    if not battle_first:
        state.observe_run(battle, 7)
    result = state.snapshot()['revision']
    assert result['workshop_stats'][0]['value'] == 100.
    assert result['workshop_levels'] is None
    assert result['workshop_stats'][0]['evidence']['frame_ref'] is None
    assert AccountState(AccountRepository(path)).snapshot()['revision'] == result
    for i in range(5):
        state.observe_account(reading(now=3.+i))
    assert state.snapshot()['revision'] == result
    assert sqlite3.connect(path).execute('select count(*) from account_revisions').fetchone()[0] == 1


def test_bad_reads_break_confirmation_and_preserve_verified(tmp_path: Path) -> None:
    from account_state import AccountRepository, AccountState
    state = AccountState(AccountRepository(tmp_path / 'state.db'))
    state.observe_account(reading())
    state.observe_account(reading(confidence=.4))
    state.observe_account(reading())
    assert state.snapshot()['revision'] is None
    state.observe_account(reading(now=2.))
    result = state.snapshot()['revision']
    state.observe_account(reading(None))
    state.observe_account(reading(float('inf')))
    assert state.snapshot()['revision'] == result
    with pytest.raises(ValueError):
        state.observe_account(reading(context='battle'))


def test_failed_write_does_not_advance_and_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from account_state import AccountRepository, AccountState
    repo = AccountRepository(tmp_path / 'state.db')
    state = AccountState(repo)
    state.observe_account(reading())
    original = repo.save_account
    def fail(*args: Any) -> None:
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(repo, 'save_account', fail)
    state.observe_account(reading(now=2.))
    assert state.snapshot()['revision'] is None
    assert 'locked' in state.snapshot()['error']
    monkeypatch.setattr(repo, 'save_account', original)
    state.observe_account(reading(now=3.))
    assert state.snapshot()['revision'] is not None
    assert state.snapshot()['error'] is None


def test_disabled_storage_is_explicit() -> None:
    from account_state import AccountState
    state = AccountState()
    state.observe_account(reading())
    state.observe_account(reading())
    assert state.snapshot()['persistence_available'] is False
    assert state.snapshot()['revision'] is None


def test_account_api_and_battle_ingestion(tmp_path: Path) -> None:
    from account_state import AccountRepository, AccountState
    from autopilot import BattleAutopilot
    from strategy import AutopilotPolicy
    from fastapi.testclient import TestClient
    from web.app import create_app
    from sinks.state import BotState
    from sinks.sse import SseSink
    from events import EventBus
    state = AccountState(AccountRepository(tmp_path / 'state.db'))
    state.observe_account(reading())
    state.observe_account(reading(now=2.))
    bot = BattleAutopilot(account_state=state)
    bot.step(None, None, AutopilotPolicy(), observation=reading(900., 'battle'), run_id=9)
    app = create_app(state=BotState(), sse=SseSink(), bus=EventBus(), db_path=None, account_state=state)
    result = TestClient(app).get('/api/account')
    assert result.status_code == 200
    assert result.json()['revision']['workshop_stats'][0]['value'] == 100.
    conn = sqlite3.connect(tmp_path / 'state.db')
    assert conn.execute('select run_id from run_observations').fetchone() == (9,)


def test_same_frame_and_long_gap_do_not_verify(tmp_path: Path) -> None:
    from account_state import AccountRepository, AccountState
    state = AccountState(AccountRepository(tmp_path / 'state.db'))
    state.observe_account(reading())
    state.observe_account(reading())
    state.observe_account(reading(now=100.))
    assert state.snapshot()['revision'] is None
    state.observe_account(reading(now=101.))
    assert state.snapshot()['revision'] is not None


@pytest.mark.parametrize('confidence', [float('nan'), float('inf'), 1.1, -.1])
def test_invalid_confidence_is_unknown(tmp_path: Path, confidence: float) -> None:
    from account_state import AccountRepository, AccountState
    state = AccountState(AccountRepository(tmp_path / 'state.db'))
    state.observe_account(reading(confidence=confidence))
    state.observe_account(reading(confidence=confidence, now=2.))
    assert state.snapshot()['revision'] is None


def test_run_rows_are_bounded_and_missing_run_id_is_not_invented(tmp_path: Path) -> None:
    from account_state import AccountRepository, AccountState
    path = tmp_path / 'state.db'
    state = AccountState(AccountRepository(path))
    state.observe_run(reading(context='battle'), None)
    for now in range(1, 100):
        state.observe_run(reading(float(now), 'battle', now=float(now)), 3)
    conn = sqlite3.connect(path)
    assert conn.execute('select count(*) from run_observations').fetchone()[0] == 10
    import json
    detail = json.loads(conn.execute('select detail from run_observations limit 1').fetchone()[0])
    assert detail['purchased_levels'] is None
    assert state.snapshot()['revision'] is None


def test_shopping_feeds_account_and_reset_breaks_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from account_state import AccountRepository, AccountState
    from shopping import ShoppingSession
    from strategy import Shopping
    from types import SimpleNamespace
    import shopping
    state = AccountState(AccountRepository(tmp_path / 'state.db'))
    session = ShoppingSession(None, SimpleNamespace(publish=lambda event: None), None)
    session.account_state = state
    session._categories = ['DEFENSE']
    monkeypatch.setattr(shopping, 'observe_frame', lambda *args: reading())
    monkeypatch.setattr(shopping, 'header_numbers', lambda *args: (None, None))
    session._buy_rows(SimpleNamespace(page='workshop', top_left=None), None, None, Shopping())
    session.reset()
    state.observe_account(reading(now=2.))
    assert state.snapshot()['revision'] is None
    session._categories = ['DEFENSE']
    monkeypatch.setattr(shopping, 'observe_frame', lambda *args: reading(now=3.))
    session._buy_rows(SimpleNamespace(page='workshop', top_left=None), None, None, Shopping())
    assert state.snapshot()['revision']['workshop_stats'][0]['value'] == 100.


def test_actual_sqlite_lock_rolls_back_and_later_recovers(tmp_path: Path) -> None:
    from account_state import AccountRepository, AccountState
    path = tmp_path / 'state.db'
    state = AccountState(AccountRepository(path))
    state.observe_account(reading())
    locked = sqlite3.connect(path)
    locked.execute('BEGIN IMMEDIATE')
    try:
        state.observe_account(reading(now=2.))
        assert state.snapshot()['revision'] is None
        assert 'locked' in state.snapshot()['error']
    finally:
        locked.rollback()
        locked.close()
    state.observe_account(reading(now=3.))
    assert state.snapshot()['revision']['revision_id'] == 1


def test_separate_process_restores_revision(tmp_path: Path) -> None:
    from account_state import AccountRepository, AccountState
    import json
    import os
    import subprocess
    import sys
    path = tmp_path / 'state.db'
    state = AccountState(AccountRepository(path))
    state.observe_account(reading())
    state.observe_account(reading(now=2.))
    command = ('import json,sys; from account_state import AccountRepository,AccountState; '
               'print(json.dumps(AccountState(AccountRepository(sys.argv[1])).snapshot()))')
    result = subprocess.run([sys.executable, '-c', command, str(path)], check=True,
                            capture_output=True, text=True, timeout=20, env={**os.environ, 'PYTHONPATH': '.'})
    assert json.loads(result.stdout)['revision'] == state.snapshot()['revision']


def test_run_success_cannot_hide_account_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from account_state import AccountRepository, AccountState
    repo = AccountRepository(tmp_path / 'state.db')
    state = AccountState(repo)
    state.observe_account(reading())
    def fail(*args: Any) -> None:
        raise sqlite3.OperationalError('account write failed')
    monkeypatch.setattr(repo, 'save_account', fail)
    state.observe_account(reading(now=2.))
    state.observe_run(reading(900., 'battle', now=3.), 1)
    assert state.snapshot()['error'] == 'account write failed'


def test_durable_run_without_telemetry_reserves_id(tmp_path: Path) -> None:
    from account_state import AccountRepository, AccountState
    from tower_bot import prepare_store
    path = tmp_path / 'state.db'
    state = AccountState(AccountRepository(path))
    state.observe_run(reading(context='battle'), 1)
    assert prepare_store(path)[1] == 1
