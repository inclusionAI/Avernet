"""Engine vetoes expose existing publish failure fields, without provider mutation."""
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import _operation_id, command
from agentclaw.community.core.service_bot.repository.models import PublishOperationState
from agentclaw.community.core.service_bot.services.publish_flow.tasks import PublishRestartHandler
from agentclaw.community.core.service_bot.types import OnlineDeployDecision
from agentclaw.community.core.task_queue.types import Fail
from tests.community.core.bot_management.aicoding.test_restart_backup import partial_response, response
from tests.community.core.bot_management.aicoding.test_restart_fastdisk import executor
from tests.community.core.service_bot.services.test_publish_crash_windows import (
    FakeBaas, _ledger, _record, _restart_flow, PublishStatus,
)


@pytest.fixture(params=['aicoding', 'claude_code'])
def published(request, monkeypatch):
    from agentclaw.community.core.bot_management.engines.registry import get_engine_provisioning_registry

    config = Mock()
    config.get_value.return_value = True
    strategy = get_engine_provisioning_registry().resolve(request.param)
    monkeypatch.setattr(strategy, "_common_config_provider", lambda: config)
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
@pytest.mark.parametrize('outcome', ['failed', 'legacy', 'not_mounted'])
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
    assert record.status == PublishStatus.SUCCESS.value
    assert '快盘' in record.ext['restart']['message']
    assert 'error_message' not in record.ext
    assert 'source_status' not in record.ext
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


@pytest.mark.asyncio
async def test_published_partial_backup_passes_enabled_guard_without_failure_fields(published, caplog):
    svc, ledger, baas, build, record = published
    build.upgrade_async = AsyncMock(side_effect=lambda **kw: {
        'success': True, 'publish_id': baas.issue('BOT-live', 'UPDATE'),
    })
    operation = _operation_id(None, 'restart:1:online')
    execute = executor([partial_response(operation_id=operation)])
    baas.post_bots_api = Mock(side_effect=lambda **kw: execute(kw['payload']['cmd']))
    result = await svc.execute_restart(1, 'online', 'operator')
    assert result['success'] is True
    build.upgrade_async.assert_awaited_once()
    assert ledger.get_latest_by_kind(1, 'restart', 'online').state == PublishOperationState.COMPLETED.value
    assert record.status == PublishStatus.SUCCESS.value
    assert not record.ext.get('error_message')
    assert 'phase=prepared status=partial_success' in caplog.text
    assert 'phase=verify status=' not in caplog.text
    assert 'AICODING_RESTART_FAILED' not in caplog.text
    assert execute.call_count == 2  # Detect, then start; no completed-receipt recheck.
    execute.assert_called_with(command('start', operation))


@pytest.mark.asyncio
async def test_published_nonblocking_failure_keeps_warning_until_completion(published, monkeypatch):
    from agentclaw.community.core.bot_management.engines.registry import (
        get_engine_provisioning_registry, resolve_published_status_ext,
    )
    svc, _, baas, build, record = published
    engine = svc._bot_service.get_bot_by_address.return_value['active_engine']
    config = Mock()
    config.get_value.return_value = False
    monkeypatch.setattr(get_engine_provisioning_registry().resolve(engine), '_common_config_provider', lambda: config)
    operation = svc.restart_bot(1, 'operator')['ext']['aicoding']['restart']['operation_id']
    execute = executor([response('failed', operation_id=operation)])
    baas.post_bots_api = Mock(side_effect=lambda **kw: execute(kw['payload']['cmd']))
    build.upgrade_async = AsyncMock(return_value={'success': True, 'publish_id': 902})

    result = await svc.execute_restart(1, 'online', 'operator', operation_id=operation)

    assert result['success'] is True
    build.upgrade_async.assert_awaited_once()
    progress = resolve_published_status_ext(record, public=True)['aicoding']['restart']
    assert progress['status'] == 'RUNNING'
    assert progress['phase'] == 'WAITING_READY'
    assert progress['warning']['code'] == 'backup_failed_continue'
    assert '数据丢失风险' in progress['warning']['message']
    assert record.status == 'success'
    assert record.ext['unrelated'] == {'keep': True}

    svc.get_baas_publish_progress = Mock(return_value={'status': 'SUCCESS'})
    svc._activate_binding = Mock()
    svc._refresh_provider_mcp_after_success = Mock()
    svc.refresh_publish_handle = Mock()
    completed = svc.sync_restart_progress(1).ext['aicoding']['restart']
    assert completed['status'] == 'SUCCEEDED'
    assert completed['warning'] == progress['warning']
    assert resolve_published_status_ext(record, public=True)['aicoding']['restart']['warning'] == progress['warning']
    # An explicit new attempt does not inherit the preceding warning.
    again = svc.restart_bot(1, 'operator')['ext']['aicoding']['restart']
    assert again['operation_id'] != operation
    assert 'warning' not in again
