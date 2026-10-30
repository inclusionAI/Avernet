"""Engine vetoes expose existing publish failure fields, without provider mutation."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import _operation_id
from agentclaw.community.core.service_bot.repository.models import PublishOperationState
from agentclaw.community.core.service_bot.services.publish_flow.tasks import PublishRestartHandler
from agentclaw.community.core.service_bot.types import OnlineDeployDecision
from agentclaw.community.core.task_queue.types import Fail
from tests.community.core.bot_management.aicoding.test_restart_backup import response
from tests.community.core.bot_management.aicoding.test_restart_fastdisk import executor
from tests.community.core.service_bot.services.test_publish_crash_windows import (
    FakeBaas, _ledger, _record, _restart_flow, PublishStatus,
)


@pytest.fixture(params=['aicoding', 'claude_code'])
def published(request):
    ledger, baas, build = _ledger(), FakeBaas(), Mock()
    record = _record(PublishStatus.SUCCESS.value)
    record.ext.update(binding={'online': 42}, unrelated={'keep': True})
    svc = _restart_flow(ledger, baas, build, record)
    svc._release_binding = Mock()
    svc._bot_service.get_bot_by_address.return_value = {
        'bot_id': 'b', 'owner_id': 'u1', 'entity_id': 'u1', 'active_engine': request.param,
    }

    def mutate(*, publish_id, mutator):
        ext = deepcopy(record.ext)
        mutator(ext)
        record.ext = ext
        return deepcopy(ext)

    def save(*, publish_id, target_status, source_status, ext, expected_ext):
        assert record.status == source_status.value
        assert record.ext == expected_ext  # Test the exact status + ext CAS contract.
        record.status, record.ext = target_status.value, deepcopy(ext)

    svc._mutate_and_update_ext.side_effect = mutate
    svc._publish_service.update_publish_status_with_ext.side_effect = save
    baas.list_devices_by_bot_uuid = Mock(return_value=[{'provider_device_id': 'ARCA-SANDBOX-published@8', 'status': 'ACTIVE'}])
    return svc, ledger, baas, build, record


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['failed', 'partial_success', 'legacy', 'not_mounted'])
@pytest.mark.parametrize('decision', [OnlineDeployDecision.UPGRADE, OnlineDeployDecision.RETIRE_THEN_FIRST_RELEASE])
async def test_real_fastdisk_veto_persists_failure_and_finishes_task(published, outcome, decision, caplog, monkeypatch):
    monkeypatch.setenv('SERVER_ENV', 'pre')
    svc, ledger, baas, build, record = published
    svc._decide_online_deploy = Mock(return_value=decision)
    operation = _operation_id(None, 'restart:1:online')
    execute = executor([response(outcome, operation_id=operation)])
    baas.post_bots_api = Mock(side_effect=lambda **kw: execute(kw['payload']['cmd']))
    queue = Mock()
    handler = PublishRestartHandler(flow=svc, task_queue_service=queue)
    assert isinstance(await handler._run(1, 'online', 'operator'), Fail)
    assert record.status == PublishStatus.FAILED.value
    assert '快盘' in record.ext['error_message']
    assert record.ext['source_status'] == PublishStatus.SUCCESS.value
    assert 'restarting' not in record.ext['restart']
    assert record.ext['binding'] == {'online': 42}
    assert record.ext['unrelated'] == {'keep': True}
    assert not baas.workflows
    build.upgrade_async.assert_not_called()
    build.retire_superseded_bot.assert_not_called()
    build.release_async.assert_not_called()
    svc._release_binding.assert_not_called()
    assert not queue.mock_calls  # No poll for a workflow that was never submitted.
    op = ledger.get_latest_by_kind(1, 'restart', 'online')
    if decision == OnlineDeployDecision.UPGRADE:
        assert op.state == PublishOperationState.FAILED.value
        assert op.baas_publish_id is None
    else:
        assert op is None
    assert isinstance(await handler._run(1, 'online', 'operator'), Fail)
    assert execute.call_count == 2  # Queue redelivery never re-detects or replaces.
    summary = next(r.getMessage() for r in caplog.records
                   if '[AICODING_RESTART_FAILED]' in r.getMessage()
                   and 'phase=published_restart' in r.getMessage())
    assert 'bot_id=b arca_id=ARCA-SANDBOX-published@8 ' in summary
    assert f'operation_id={operation}' in summary
    assert f'helper_status={outcome}' in summary
    assert 'publish_id=1' in summary
    assert 'env=pre owner_id=u1 ' in summary  # Bot owner, not restart operator.


@pytest.mark.asyncio
async def test_veto_status_persistence_failure_is_not_swallowed(published, caplog):
    svc, _, baas, build, record = published
    operation = _operation_id(None, 'restart:1:online')
    execute = executor([response('failed', operation_id=operation)])
    baas.post_bots_api = Mock(side_effect=lambda **kw: execute(kw['payload']['cmd']))
    svc._publish_service.update_publish_status_with_ext.side_effect = RuntimeError('database unavailable')
    with pytest.raises(RuntimeError, match='database unavailable'):
        await svc.execute_restart(1, 'online', 'operator')
    build.upgrade_async.assert_not_called()
    assert record.status == PublishStatus.SUCCESS.value
    assert '[AICODING_RESTART_FAILED]' in caplog.text
    assert 'phase=published_restart' in caplog.text
    assert 'arca_id=ARCA-SANDBOX-published@8 ' in caplog.text


@pytest.mark.asyncio
async def test_veto_cannot_fail_concurrent_restart_marker(published):
    svc, _, baas, build, record = published
    operation = _operation_id(None, 'restart:1:online')
    execute = executor([response('failed', operation_id=operation)])

    def command(**kw):
        result = execute(kw['payload']['cmd'])
        record.ext['restart']['guard']['id'] = 'newer-restart'
        return result

    baas.post_bots_api = Mock(side_effect=command)
    result = await svc.execute_restart(1, 'online', 'operator')
    assert not result['success']
    assert record.status == PublishStatus.SUCCESS.value
    assert record.ext['restart']['restarting'] is True
    svc._publish_service.update_publish_status_with_ext.assert_not_called()
    build.upgrade_async.assert_not_called()
