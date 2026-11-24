import inspect
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.adapters.http.openapi_v1.managed_bots.router import router
from agentclaw.community.adapters.http.openapi_v1.middleware import (
    SensitiveResponseHeaderMiddleware,
)
from agentclaw.community.api.managed_bots import ManagedBotServiceProtocol
from agentclaw.community.di.openapi_bot_operations_config import (
    OpenApiBotOperationsConfig,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
)
from tests.community.adapters.http.openapi_v1.test_principal_seam import mint
from tests.community.adapters.http.openapi_v1.test_principal_seam import (
    signing_key as signing_key,
)


@pytest.fixture
def operations_client():
    service = Mock(spec=ManagedBotServiceProtocol)
    service.restart = AsyncMock(return_value={'bot_id': 'bot', 'status': 'ACTIVE', 'active_engine': 'aicoding'})
    service.query.return_value = {'total': 0, 'items': []}
    service.snapshot_context.return_value = {
        'template_config': {'template_key': 'coding'}, 'template_config_hash': 'a' * 64,
    }
    service.statuses.return_value = [{'bot_id': 'bot', 'owner_id': 'owner', 'active_engine': 'aicoding',
        'status': 'ACTIVE', 'is_ready': True,
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
    from agentclaw.community.adapters.http.error_logging import (
        capture_call_params,
        format_call_params,
    )
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


@pytest.mark.parametrize('operation,payload', [
    ('template-queries', {'template_key': 'coding'}),
    ('template-config-for-others', {'template_key': 'coding', 'bot_id': 'bot', 'owner_id': 'owner'}),
    ('restart-for-others', {'template_key': 'coding', 'bot_id': 'bot', 'owner_id': 'owner'}),
    ('status-for-others', {'template_key': 'coding', 'bots': [{'bot_id': 'bot', 'owner_id': 'owner'}]}),
])
@pytest.mark.parametrize('identity', [
    {'user_id': 'human'},
    {'tenant': None},
    {'user_id': None, 'tenant': 'untrusted-tenant'},
])
def test_managed_operations_reject_human_mixed_and_untrusted_application_identities(
    operations_client, operation, payload, identity,
):
    client, service = operations_client
    response = client.post(
        '/openapi/v1/bots/' + operation,
        headers={PRINCIPAL_HEADER: mint(**identity)},
        json=payload,
    )
    assert response.status_code == 403
    assert not service.mock_calls


@pytest.mark.parametrize('operation,payload', [
    ('template-queries', {'template_key': 'coding'}),
    ('template-config-for-others', {'template_key': 'coding', 'bot_id': 'bot', 'owner_id': 'owner'}),
    ('restart-for-others', {'template_key': 'coding', 'bot_id': 'bot', 'owner_id': 'owner'}),
    ('status-for-others', {'template_key': 'coding', 'bots': [{'bot_id': 'bot', 'owner_id': 'owner'}]}),
])
def test_managed_operations_admit_the_allowlisted_application_only_identity(operations_client, operation, payload):
    client, _ = operations_client
    response = client.post('/openapi/v1/bots/' + operation, headers=headers(), json=payload)
    assert response.status_code == 200


@pytest.mark.parametrize('receipt_id,expected_operation', [(None, None), ('different', None), ('old', 'old')])
def test_submission_only_reports_the_operation_explicitly_identified_by_lifecycle(
    operations_client, receipt_id, expected_operation,
):
    client, service = operations_client
    service.restart.return_value = {
        'status': 'PENDING',
        'active_engine': 'aicoding',
        'device_binding': {'status': 'PENDING'},
        'restart_operation_id': receipt_id,
        'ext': {'coding_restart': {'operation_id': 'old', 'phase': 'SUCCEEDED'}},
    }
    response = client.post('/openapi/v1/bots/restart-for-others', headers=headers(), json={
        'bot_id': 'bot', 'owner_id': 'owner', 'template_key': 'coding',
    })
    assert response.status_code == 200
    data = response.json()['data']
    restart = ((data.get('ext') or {}).get('aicoding') or {}).get('restart')
    assert (restart or {}).get('operation_id') == expected_operation
    assert service.restart.return_value['ext']['coding_restart']['operation_id'] == 'old'


@pytest.mark.parametrize('operation', ['restart-for-others', 'status-for-others'])
@pytest.mark.parametrize('engine', ['claude_code', 'aicoding'])
def test_managed_engine_gate_accepts_both_coding_engines(operations_client, operation, engine):
    client, service = operations_client
    service.statuses.return_value[0]['active_engine'] = engine
    payload = {'template_key': 'coding', 'bot_id': 'bot', 'owner_id': 'owner'}
    if operation == 'status-for-others':
        payload = {'template_key': 'coding', 'bots': [{'bot_id': 'bot', 'owner_id': 'owner'}]}

    response = client.post('/openapi/v1/bots/' + operation, headers=headers(), json=payload)

    assert response.status_code == 200
    service.statuses.assert_called_once_with(template_key='coding', targets=[('bot', 'owner')])
    if operation == 'status-for-others':
        assert 'active_engine' not in response.json()['data']['items'][0]


@pytest.mark.parametrize('operation', ['restart-for-others', 'status-for-others'])
@pytest.mark.parametrize('engine', ['openclaw', 'teclaw', 'unknown', '', None, 'AICODING'])
@pytest.mark.parametrize('with_snapshot', [False, True])
def test_managed_engine_gate_refuses_other_or_unknown_engines(operations_client, operation, engine, with_snapshot):
    client, service = operations_client
    service.statuses.return_value[0]['active_engine'] = engine
    payload = {'template_key': 'coding', 'bot_id': 'bot', 'owner_id': 'owner'}
    if operation == 'status-for-others':
        payload = {'template_key': 'coding', 'bots': [{'bot_id': 'bot', 'owner_id': 'owner'}]}
    elif with_snapshot:
        payload['extra_configs'] = {
            'template_config': {'template_key': 'coding', 'template_version_id': 4},
            'confirmed_template_update': 'true', 'expected_template_config_hash': 'a' * 64,
        }

    response = client.post('/openapi/v1/bots/' + operation, headers=headers(), json=payload)

    assert response.status_code == 501
    assert response.json()['code'] == 501000
    assert response.json()['data'] is None
    service.restart.assert_not_awaited()


def test_status_engine_gate_refuses_the_whole_mixed_batch(operations_client):
    client, service = operations_client
    service.statuses.return_value.append({
        'bot_id': 'other', 'owner_id': 'owner', 'active_engine': 'openclaw',
        'status': 'ACTIVE', 'is_ready': True,
    })

    response = client.post('/openapi/v1/bots/status-for-others', headers=headers(), json={
        'template_key': 'coding',
        'bots': [{'bot_id': 'bot', 'owner_id': 'owner'}, {'bot_id': 'other', 'owner_id': 'owner'}],
    })

    assert response.status_code == 501
    assert response.json()['data'] is None
    service.statuses.assert_called_once()


@pytest.mark.parametrize('operation,expected_status', [('restart-for-others', 404), ('status-for-others', 200)])
def test_engine_gate_preserves_missing_target_semantics(operations_client, operation, expected_status):
    client, service = operations_client
    service.statuses.return_value = []
    payload = {'template_key': 'coding', 'bot_id': 'bot', 'owner_id': 'owner'}
    if operation == 'status-for-others':
        payload = {'template_key': 'coding', 'bots': [{'bot_id': 'bot', 'owner_id': 'owner'}]}

    response = client.post('/openapi/v1/bots/' + operation, headers=headers(), json=payload)

    assert response.status_code == expected_status
    service.restart.assert_not_awaited()
    if operation == 'status-for-others':
        assert response.json()['data'] == {'total': 0, 'items': []}


def test_engine_gate_leaves_discovery_and_snapshot_reads_unrestricted(operations_client):
    client, service = operations_client
    service.snapshot_context.return_value['active_engine'] = 'openclaw'

    assert client.post('/openapi/v1/bots/template-queries', headers=headers(), json={
        'template_key': 'coding',
    }).status_code == 200
    assert client.post('/openapi/v1/bots/template-config-for-others', headers=headers(), json={
        'template_key': 'coding', 'bot_id': 'bot', 'owner_id': 'owner',
    }).status_code == 200
    service.statuses.assert_not_called()


def test_managed_engine_restrictions_are_documented_without_changing_request_bodies():
    app = FastAPI()
    app.include_router(router)
    document = app.openapi()

    for operation, schema in [
        ('restart-for-others', 'ManagedBotRestart'), ('status-for-others', 'ManagedBotStatusQueries'),
    ]:
        entry = document['paths']['/openapi/v1/bots/' + operation]['post']
        assert entry['requestBody']['content']['application/json']['schema'] == {
            '$ref': '#/components/schemas/' + schema,
        }
        assert entry['responses']['501']['content']['application/json']['schema'] == {
            '$ref': '#/components/schemas/ErrorEnvelope',
        }


@pytest.mark.parametrize('operation', ['restart-for-others', 'status-for-others'])
def test_engine_gate_fails_closed_when_the_engine_field_is_missing(operations_client, operation):
    client, service = operations_client
    service.statuses.return_value[0].pop('active_engine')
    payload = {'template_key': 'coding', 'bot_id': 'bot', 'owner_id': 'owner'}
    if operation == 'status-for-others':
        payload = {'template_key': 'coding', 'bots': [{'bot_id': 'bot', 'owner_id': 'owner'}]}

    response = client.post('/openapi/v1/bots/' + operation, headers=headers(), json=payload)

    assert response.status_code == 501
    service.restart.assert_not_awaited()


def test_engine_gate_composes_with_the_existing_owner_aspect(monkeypatch):
    from agentclaw.community.adapters.http.openapi_v1.admission import (
        ADMISSION, AdmissionMode, AdmissionRule,
    )
    from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

    async def targets():
        return [{'active_engine': 'aicoding'}]

    monkeypatch.setitem(ADMISSION, ('GET', '/openapi/v1/bots/{bot_id}/status'), AdmissionRule(
        mode=AdmissionMode.ENGINE, engines=frozenset({'aicoding'}), bot_dependency=lambda: targets,
    ))

    async def handler():
        return {}

    route = PublicAPIRoute('/openapi/v1/bots/{bot_id}/status', handler, methods={'GET'})
    gate_modules = {dependency.call.__module__ for dependency in route.dependant.dependencies}

    assert 'agentclaw.community.adapters.http.openapi_v1.bot_access' in gate_modules
    assert 'agentclaw.community.adapters.http.openapi_v1.admission' in gate_modules
    assert 501 in route.responses


def test_unrestricted_owner_route_does_not_acquire_an_engine_gate():
    from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

    async def handler():
        return {}

    route = PublicAPIRoute('/openapi/v1/bots/{bot_id}/status', handler, methods={'GET'})

    assert all(not dependency.call.__module__.endswith('.admission') for dependency in route.dependant.dependencies)
    assert 501 not in route.responses
