"""Coding strategy consumes the existing grouped BaaS devices API."""
import json
import logging
from unittest.mock import Mock, call

import httpx
import pytest

from agentclaw.community.core.bot_management.engines.aicoding import restart_backup as backup
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext
from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError
from tests.community.core.service_bot.services.test_baas_service_exec_command import _make_service

BOT = 'BOT-test'
DEVICES = [{'status': 'ACTIVE', 'provider_device_id': 'physical'}]
GROUPS = [{'items': DEVICES, 'total': 1, 'page': 1, 'page_size': 1}]


def setup_service(*payloads):
    service, http = _make_service()
    responses = []
    for payload in payloads:
        response = Mock()
        response.json.return_value = {'code': 0, 'data': payload}
        responses.append(response)
    http.get.side_effect = responses
    return service, http


def query(service, phase='resolve'):
    return backup._query_inventory(service, bot_id='b', target_id=BOT,
                                   operation_id='a' * 32, phase=phase)


def test_strategy_uses_existing_devices_endpoint_and_logs_summary(caplog):
    caplog.set_level(logging.INFO, logger=backup.logger.name)
    service, http = setup_service(GROUPS)
    assert query(service) == {'physical': DEVICES[0]}
    http.get.assert_called_once_with(f'/api/v1/bots/{BOT}/devices',
                                    params={'tenant': service._tenant}, timeout=30.0)
    for text in ('inventory=query_started', 'inventory=observed', 'raw_device_count=1',
                 "raw_status_counts={'ACTIVE': 1}", 'source=baas_devices', 'elapsed_ms=',
                 'operation_id=' + 'a' * 32, 'target_count=1'):
        assert text in caplog.text


def test_historical_groups_are_not_flattened():
    history = {'items': [{'status': 'ACTIVE', 'provider_device_id': 'old'}]}
    service, _ = setup_service(GROUPS + [history])
    assert set(query(service)) == {'physical'}


@pytest.mark.parametrize('groups', [[], [{'items': []}], [{'items': None}]])
def test_empty_or_invalid_inventory_blocks(groups, caplog):
    service, http = setup_service(groups)
    with pytest.raises((RuntimeError, BaasServiceError)):
        query(service)
    assert ('inventory=blocked' in caplog.text or 'inventory=query_failed' in caplog.text)
    http.post.assert_not_called()


@pytest.mark.parametrize('phase', ['resolve', 'verify'])
def test_query_failure_logs_phase_without_exception_body(phase, caplog):
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.side_effect = TimeoutError('secret-output')
    with pytest.raises(TimeoutError):
        query(runtime, phase)
    assert 'phase=' + phase in caplog.text
    assert 'inventory=query_failed' in caplog.text
    assert 'error_type=TimeoutError' in caplog.text
    assert 'secret-output' not in caplog.text


@pytest.mark.parametrize('status', [404, 500])
def test_http_failure_never_means_released(status):
    service, http = setup_service()
    http.get.side_effect = httpx.HTTPStatusError(
        'failed', request=httpx.Request('GET', 'http://test'), response=httpx.Response(status))
    with pytest.raises(BaasServiceError):
        query(service)


@pytest.mark.parametrize('status', ['legacy', 'not_mounted'])
def test_real_client_reaches_container_skip_and_rechecks_inventory(status):
    service, http = setup_service(GROUPS, GROUPS)
    operation = 'a' * 32
    http.post.return_value.json.return_value = {'code': 0, 'data': {
        'exit_code': 0,
        'stdout': json.dumps({'version': 1, 'operation_id': operation,
                              'boot_id': 'boot-1', 'status': status}),
    }}
    ctx = BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding')
    verify = AicodingProvisioningStrategy('aicoding')._prepare_restart(
        ctx, device_id=BOT, target_runtime=service, operation_id=operation)
    verify()
    assert http.get.call_args_list == 2 * [
        call(f'/api/v1/bots/{BOT}/devices', params={'tenant': service._tenant}, timeout=30.0)]
    assert http.post.call_count == 2
    assert all(c.args[0] == '/api/v1/paas/devices/physical/commands'
               for c in http.post.call_args_list)
