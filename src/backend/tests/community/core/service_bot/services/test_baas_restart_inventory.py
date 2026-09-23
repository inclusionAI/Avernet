"""Actual BaaS response shapes: UUID detail is not a device inventory."""
import json
from unittest.mock import Mock, call

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError
from tests.community.core.service_bot.services.test_baas_service_exec_command import _make_service
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext


BOT = 'BOT-test'
SUMMARY = {'id': 123, 'bot_uuid': BOT, 'status': 'ACTIVE', 'devices': []}
DETAIL = dict(SUMMARY, devices=[{'status': 'ACTIVE', 'provider_device_id': 'physical'}])


def setup_service(*payloads):
    service, http = _make_service()
    responses = []
    for payload in payloads:
        response = Mock()
        response.json.return_value = {'code': 0, 'data': payload}
        responses.append(response)
    http.get.side_effect = responses
    return service, http


def test_inventory_uses_current_record_detail_without_health_check():
    service, http = setup_service(SUMMARY, DETAIL)
    assert service.get_bot(BOT, include_devices=True) == DETAIL
    assert http.get.call_args_list == [
        call(f'/api/v1/bots/{BOT}', params={'tenant': service._tenant}, timeout=30.0),
        call('/api/v1/bots/123/detail-by-id', params={'tenant': service._tenant}, timeout=30.0),
    ]


def test_default_get_bot_is_unchanged():
    service, http = setup_service(SUMMARY)
    assert service.get_bot(BOT) == SUMMARY
    assert http.get.call_count == 1


@pytest.mark.parametrize('detail', [None, {}, dict(DETAIL, id=456),
    dict(DETAIL, bot_uuid='BOT-other'), dict(DETAIL, devices=None)])
def test_invalid_detail_blocks(detail):
    service, _ = setup_service(SUMMARY, detail)
    with pytest.raises(BaasServiceError):
        service.get_bot(BOT, include_devices=True)


@pytest.mark.parametrize('summary', [None, {}, dict(SUMMARY, id=True), dict(SUMMARY, id=-1)])
def test_missing_id_does_not_query_random_record(summary):
    service, http = setup_service(summary)
    with pytest.raises(BaasServiceError):
        service.get_bot(BOT, include_devices=True)
    assert http.get.call_count == 1


@pytest.mark.parametrize('status', [404, 500])
@pytest.mark.parametrize('on_detail', [False, True])
def test_inventory_http_failure_never_means_released(status, on_detail):
    service, http = setup_service(SUMMARY)
    error = httpx.HTTPStatusError('failed', request=httpx.Request('GET', 'http://test'),
                                response=httpx.Response(status))
    first = http.get.side_effect
    http.get.side_effect = [next(first), error] if on_detail else error
    with pytest.raises(BaasServiceError):
        service.get_bot(BOT, include_devices=True)


@pytest.mark.parametrize('status', ['legacy', 'not_mounted'])
def test_real_client_empty_summary_reaches_container_skip(status):
    service, http = setup_service(SUMMARY, DETAIL, SUMMARY, DETAIL)
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
    assert http.get.call_count == 4
    assert http.post.call_count == 2
    assert all(c.args[0] == '/api/v1/paas/devices/physical/commands'
               for c in http.post.call_args_list)
