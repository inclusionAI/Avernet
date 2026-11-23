from unittest.mock import AsyncMock, Mock
import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.adapters.http.openapi_v1.managed_bots.router import router
from agentclaw.community.adapters.http.openapi_v1.middleware import SensitiveResponseHeaderMiddleware
from agentclaw.community.api.managed_bots import ManagedBotServiceProtocol
from agentclaw.community.di.openapi_bot_operations_config import OpenApiBotOperationsConfig
from tests.community.adapters.http.openapi_v1.conftest import mount_public_error_handlers
from tests.community.adapters.http.openapi_v1.test_principal_seam import mint, signing_key


@pytest.fixture
def operations_client():
    service = Mock(spec=ManagedBotServiceProtocol)
    service.restart = AsyncMock(return_value={'bot_id': 'bot', 'status': 'ACTIVE', 'active_engine': 'aicoding'})
    service.statuses.return_value = [{'bot_id': 'bot', 'owner_id': 'owner', 'status': 'ACTIVE', 'is_ready': True,
        'ext': {'aicoding': {'restart': {'operation_id': 'op', 'status': 'FAILED', 'phase': 'BACKING_UP',
            'message': 'Backup failed', 'warning': {'code': 'backup_failed_continue', 'message': 'Risk'}}}}}]
    app = mount_public_error_handlers(FastAPI())
    app.add_middleware(SensitiveResponseHeaderMiddleware)
    app.include_router(router)
    injector = Injector()
    injector.binder.bind(ManagedBotServiceProtocol, to=service)
    injector.binder.bind(OpenApiBotOperationsConfig, to=OpenApiBotOperationsConfig(
        frozenset({('dev', 'acme-tenant', 'bot-logs-test-app')})))
    attach_injector(app, injector)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service


def headers():
    return {PRINCIPAL_HEADER: mint(user_id=None)}


def test_restart_is_200_acceptance_and_forwards_snapshot(operations_client):
    client, service = operations_client
    response = client.post('/openapi/v1/bots/restart-for-others', headers=headers(), json={
        'bot_id': 'bot', 'owner_id': 'owner', 'template_key': 'coding',
        'extra_configs': {'template_config': {'template_key': 'coding', 'template_version_id': 4},
                          'confirmed_template_update': 'true', 'expected_template_config_hash': 'a' * 64},
    })
    assert response.status_code == 200
    assert response.json()['code'] == 200000
    assert response.json()['message'] == 'OK'
    service.restart.assert_awaited_once_with(bot_id='bot', owner_id='owner', template_key='coding',
        template_config={'template_key': 'coding', 'template_version_id': 4}, expected_template_config_hash='a' * 64)


def test_status_keeps_operation_failure_and_warning_under_successful_query(operations_client):
    client, _ = operations_client
    response = client.post('/openapi/v1/bots/status-for-others', headers=headers(), json={
        'template_key': 'coding', 'bots': [{'bot_id': 'bot', 'owner_id': 'owner'}],
    })
    assert response.status_code == 200
    assert response.json()['data']['total'] == 1
    assert response.json()['data']['items'][0]['ext']['aicoding']['restart']['status'] == 'FAILED'


def test_unknown_app_or_human_is_refused_before_target_lookup(operations_client):
    client, service = operations_client
    response = client.post('/openapi/v1/bots/restart-for-others',
        headers={PRINCIPAL_HEADER: mint(tenant=None)}, json={
            'bot_id': 'bot', 'owner_id': 'owner', 'template_key': 'coding',
        })
    assert response.status_code == 403
    service.restart.assert_not_called()


def test_published_stage_and_oversized_batch_are_refused(operations_client):
    client, service = operations_client
    response = client.post('/openapi/v1/bots/restart-for-others', headers=headers(), json={
        'bot_id': 'bot', 'owner_id': 'owner', 'template_key': 'coding', 'stage': 'online',
    })
    assert response.status_code == 422
    assert client.post('/openapi/v1/bots/status-for-others', headers=headers(), json={
        'template_key': 'coding', 'bots': [{'bot_id': str(index), 'owner_id': 'owner'} for index in range(21)],
    }).status_code == 422
    service.restart.assert_not_called()


def test_snapshot_is_an_opaque_logging_field_even_with_unrecognised_secret_names():
    from agentclaw.community.adapters.http.error_logging import capture_call_params, format_call_params
    def handler(body):
        return body
    payload = {'bot_id': 'bot', 'extra_configs': {'template_config': {
        'envs': {'DATABASE_URL': 'postgres://user:private-password@example.invalid/db'},
    }}}
    rendered = format_call_params(capture_call_params(inspect.signature(handler), (payload,), {}))
    assert 'postgres://' not in rendered
    assert 'private-password' not in rendered


def test_scope_refusal_is_definitive_404_not_uncertain_500(operations_client):
    from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
    client, service = operations_client
    service.restart.side_effect = BotAddressNotFoundError('not in requested scope')
    response = client.post('/openapi/v1/bots/restart-for-others', headers=headers(), json={
        'bot_id': 'bot', 'owner_id': 'owner', 'template_key': 'coding',
    })
    assert response.status_code == 404


def test_sensitive_snapshot_response_cannot_be_cached(operations_client):
    client, service = operations_client
    service.snapshot_context.return_value = {'template_config': {'template_key': 'coding'}, 'template_config_hash': 'a' * 64}
    response = client.post('/openapi/v1/bots/template-config-for-others', headers=headers(), json={
        'bot_id': 'bot', 'owner_id': 'owner', 'template_key': 'coding',
    })
    assert response.status_code == 200
    assert 'no-store' in response.headers['cache-control']
