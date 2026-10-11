from pathlib import Path

from fastapi.testclient import TestClient
from fastapi import FastAPI

from tests.test_strategy_service import target_at
from strategy_service import StrategyService


def test_generic_single_assignment_requires_current_identity(tmp_path: Path) -> None:
    from web.strategy_api import register_strategy_routes
    target = target_at(tmp_path)
    service = StrategyService(library_root=target.library_root,
        assignment_root=target.assignment_root, resolve_target=lambda _: target)
    app = FastAPI()
    register_strategy_routes(app, service=service, local_target=lambda: target)
    client = TestClient(app)
    assert client.get('/api/strategy-studio/status').json()['state'] == 'legacy'
    labs = client.get('/api/strategy-studio/labs')
    assert labs.status_code == 200
    assert labs.json()['workers'][0]['freshness'] == 'unknown'
    template = client.get('/api/strategy-studio/library').json()['templates'][0]
    payload = {'strategy_id': template['id'], 'strategy_version': 1, 'expected_revision': 0,
        'target_id': 'standalone', 'account_id': target.account_id,
        'generation': 'old', 'epoch': 0}
    assert client.post('/api/strategy-studio/assign', json=payload).status_code == 409
    payload['generation'] = target.scope.generation
    assert client.post('/api/strategy-studio/assign', json=payload).json()['status'] == 'pending'


def test_labs_proxy_uses_full_scoped_api_path(tmp_path: Path) -> None:
    from web.strategy_api import register_strategy_routes
    target = target_at(tmp_path)
    service = StrategyService(library_root=target.library_root,
        assignment_root=target.assignment_root, resolve_target=lambda _: target)
    seen: list[str] = []
    def remote(request, path, body):
        seen.append(path)
        return {'workers': [], 'automated': [], 'reference': None}
    app = FastAPI()
    register_strategy_routes(app, service=service, local_target=lambda: target, remote=remote)
    assert TestClient(app).get('/api/strategy-studio/labs').status_code == 200
    assert seen == ['/api/strategy-studio/labs']


def test_workshop_preview_returns_current_and_proposed_decisions(tmp_path: Path) -> None:
    from web.strategy_api import register_strategy_routes
    target = target_at(tmp_path)
    service = StrategyService(library_root=target.library_root,
        assignment_root=target.assignment_root, resolve_target=lambda _: target)
    assigned = service.assign(expected_revision=0, strategy_id='opening',
        strategy_version=1, targets=[target])
    app = FastAPI()
    register_strategy_routes(app, service=service, local_target=lambda: target)

    response = TestClient(app).post('/api/strategy-studio/preview', json={
        'expected_revision': assigned['route']['revision'], 'route': assigned['route']})

    assert response.status_code == 200, response.json()
    preview = response.json()
    assert preview['saved_revision'] == 1
    assert preview['proposed_revision'] == 2
    member = preview['members'][0]
    assert member['account_id'] == target.account_id
    assert member['worker'] == target.target_id
    assert member['current']['account_id'] == target.account_id
    assert member['proposed']['account_id'] == target.account_id
