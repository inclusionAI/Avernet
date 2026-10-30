"""Published restart crosses the same engine seam for coding and default policies."""
from asyncio import CancelledError
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import RestartBackupBlocked
from agentclaw.community.core.bot_management.engines.default import DefaultProvisioningStrategy
from agentclaw.community.core.bot_management.engines.registry import resolve_restart_strategy
from agentclaw.community.core.service_bot.repository.models import PublishOperationKind, PublishOperationState
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    PublishOperationRunner, acquire_deploy_workflow,
)
from agentclaw.community.core.service_bot.types import PublishStage
from tests.community.core.service_bot.services.test_publish_crash_windows import (
    FakeBaas, PublishStatus, _crash_before_record, _ledger, _record, _restart_flow,
)


def _setup(engine):
    ledger, baas, build = _ledger(), FakeBaas(), Mock()
    record = _record(PublishStatus.SUCCESS.value)
    record.ext['binding'] = {'online': 42}
    svc = _restart_flow(ledger, baas, build, record)
    svc._bot_service.get_bot_by_address.return_value = {
        'bot_id': 'b', 'entity_id': 'u1', 'active_engine': engine,
    }

    def mutate(*, publish_id, mutator):
        ext = deepcopy(record.ext)
        mutator(ext)
        record.ext = ext
        return deepcopy(ext)

    svc._mutate_and_update_ext.side_effect = mutate
    baas.list_devices_by_bot_uuid = Mock(side_effect=AssertionError('must not probe'))
    baas.post_bots_api = Mock(side_effect=AssertionError('must not execute'))
    build.upgrade_async = AsyncMock(side_effect=lambda **kw: {
        'success': True, 'publish_id': baas.issue('BOT-live', 'UPDATE'),
    })
    return svc, ledger, baas, build, record


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['openclaw', 'hermes', 'moltis', 'teclaw', 'other-engine', None])
async def test_default_restart_keeps_existing_state_and_never_runs_coding_policy(engine):
    svc, ledger, baas, build, record = _setup(engine)
    assert (await svc.execute_restart(1, 'online', 'operator'))['success']
    assert record.status == PublishStatus.SUCCESS.value
    assert record.ext['restart'] == {'restarting': True, 'online': 901}
    svc._publish_service.update_publish_status_with_ext.assert_not_called()
    baas.list_devices_by_bot_uuid.assert_not_called()
    baas.post_bots_api.assert_not_called()
    build.upgrade_async.assert_awaited_once()
    assert ledger.get_latest_by_kind(1, 'restart', 'online').state == PublishOperationState.COMPLETED.value


@pytest.mark.asyncio
async def test_shared_flow_calls_registered_strategy_not_a_concrete_engine():
    svc, _, _, build, record = _setup('other-engine')
    calls = []

    class OtherPolicy(DefaultProvisioningStrategy):
        @contextmanager
        def published_restart_scope(self, ctx, **kwargs):
            calls.append('enter')
            with super().published_restart_scope(ctx, **kwargs) as outcome:
                try:
                    yield outcome
                finally:
                    calls.append('exit')

        async def acquire_restart_workflow(self, ctx, op, issue, *, runner):
            calls.append('acquire')
            return await super().acquire_restart_workflow(ctx, op, issue, runner=runner)

    ctx, _ = resolve_restart_strategy(svc._bot_service.get_bot_by_address.return_value)
    with patch('agentclaw.community.core.service_bot.services.publish_flow.restart_mixin.resolve_restart_strategy',
               return_value=(ctx, OtherPolicy())):
        assert (await svc.execute_restart(1, 'online', 'operator'))['success']
    assert calls == ['enter', 'acquire', 'exit']
    build.upgrade_async.assert_awaited_once()
    assert 'guard' not in record.ext['restart']


@pytest.mark.asyncio
async def test_shared_runner_does_not_classify_coding_errors_without_a_strategy():
    ledger, baas = _ledger(), FakeBaas()
    runner = PublishOperationRunner(ledger=ledger, baas_service=baas)
    error = RestartBackupBlocked('backup_failed')
    with pytest.raises(RestartBackupBlocked) as caught:
        await acquire_deploy_workflow(
            runner, publish_id=1, kind=PublishOperationKind.RESTART,
            stage=PublishStage.ONLINE, operator='operator', bot_uuid='BOT-live',
            issue=AsyncMock(side_effect=error),
        )
    assert caught.value is error
    assert ledger.get_latest_by_kind(1, 'restart', 'online').state == PublishOperationState.PENDING.value


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
async def test_coding_policy_closes_exact_operation_not_latest(engine):
    ledger, baas = _ledger(), FakeBaas()
    runner = PublishOperationRunner(ledger=ledger, baas_service=baas)
    ctx, strategy = resolve_restart_strategy({'active_engine': engine})
    owned = runner.open_operation(publish_id=1, kind=PublishOperationKind.RESTART,
                                  stage=PublishStage.ONLINE, bot_uuid='BOT-live')
    newer = None

    async def issue():
        nonlocal newer
        runner.abandon_operation(owned, 'concurrent new request')
        newer = runner.open_operation(publish_id=1, kind=PublishOperationKind.RESTART,
                                      stage=PublishStage.ONLINE, bot_uuid='BOT-live')
        raise RestartBackupBlocked('backup_failed')

    with pytest.raises(RestartBackupBlocked):
        await strategy.acquire_restart_workflow(ctx, owned, issue, runner=runner)
    assert newer.id != owned.id
    assert ledger.get_by_id(newer.id).state == PublishOperationState.PENDING.value
    assert not baas.workflows


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
async def test_coding_policy_still_adopts_after_post_submit_persistence_crash(engine):
    svc, ledger, baas, build, record = _setup(engine)
    # Detection and backup unavailable: retain the historical best-effort policy.
    baas.list_devices_by_bot_uuid.side_effect = None
    baas.list_devices_by_bot_uuid.return_value = [{'provider_device_id': 'physical', 'status': 'ACTIVE'}]
    baas.post_bots_api.side_effect = RuntimeError('unreachable sandbox')
    _crash_before_record(ledger)
    with pytest.raises(RuntimeError, match='pod died after issue'):
        await svc.execute_restart(1, 'online', 'operator')
    assert ledger.get_latest_by_kind(1, 'restart', 'online').state == PublishOperationState.PENDING.value
    assert record.status == PublishStatus.SUCCESS.value
    probes = baas.post_bots_api.call_count
    assert (await svc.execute_restart(1, 'online', 'operator'))['success']
    build.upgrade_async.assert_awaited_once()
    assert baas.post_bots_api.call_count == probes
    svc._publish_service.update_publish_status_with_ext.assert_not_called()
    assert ledger.get_latest_by_kind(1, 'restart', 'online').state == PublishOperationState.COMPLETED.value


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.parametrize('detector_fails', [False, True])
async def test_nonfastdisk_or_unknown_detection_backup_failure_still_restarts(engine, detector_fails):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import _operation_id
    from tests.community.core.bot_management.aicoding.test_restart_backup import response
    from tests.community.core.bot_management.aicoding.test_restart_fastdisk import detection, executor

    svc, _, baas, build, record = _setup(engine)
    baas.list_devices_by_bot_uuid.side_effect = None
    baas.list_devices_by_bot_uuid.return_value = [{'provider_device_id': 'physical', 'status': 'ACTIVE'}]
    detected = RuntimeError('detector unavailable') if detector_fails else detection('skip_no_fastdisk')
    execute = executor([response('failed', operation_id=_operation_id(None, 'restart:1:online'))],
                       detected=detected)
    baas.post_bots_api.side_effect = lambda **kw: execute(kw['payload']['cmd'])
    assert (await svc.execute_restart(1, 'online', 'operator'))['success']
    build.upgrade_async.assert_awaited_once()
    assert execute.call_count == 2
    assert record.status == PublishStatus.SUCCESS.value
    assert 'error_message' not in record.ext
    assert 'precondition_error' not in record.ext['restart']


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['openclaw', 'other-engine'])
async def test_default_strategy_never_reads_or_writes_failure_state(engine):
    ctx, strategy = resolve_restart_strategy({'active_engine': engine})
    services = Mock()
    error = RuntimeError('original provider failure')
    restart = AsyncMock(side_effect=error)
    with pytest.raises(RuntimeError) as caught:
        with strategy.published_restart_scope(
            ctx, services=services, publish_id=1, stage='online',
            binding_id=42, source_status=PublishStatus.SUCCESS.value,
        ) as outcome:
            assert outcome == {}
            await restart()
    assert caught.value is error
    restart.assert_awaited_once_with()
    assert not services.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize('when', ['before', 'error'])
async def test_shared_flow_uses_another_engine_scope_result_without_classifying_it(when):
    svc, _, baas, build, record = _setup('other-engine')
    before = deepcopy(record.ext)
    expected = {'success': False, 'message': 'another engine policy'}
    error = RuntimeError('provider unavailable')
    build.upgrade_async.side_effect = error
    calls = []

    class OtherPolicy(DefaultProvisioningStrategy):
        @contextmanager
        def published_restart_scope(self, ctx, **kwargs):
            outcome = dict(expected) if when == 'before' else {}
            calls.append('enter')
            try:
                yield outcome
            except RuntimeError as caught:
                assert caught is error
                calls.append('error')
                outcome.update(expected)
            finally:
                calls.append('exit')

    ctx, _ = resolve_restart_strategy(svc._bot_service.get_bot_by_address.return_value)
    with patch('agentclaw.community.core.service_bot.services.publish_flow.restart_mixin.resolve_restart_strategy',
               return_value=(ctx, OtherPolicy())):
        assert await svc.execute_restart(1, 'online', 'operator') == expected
    if when == 'before':
        assert calls == ['enter', 'exit']
        assert record.ext == before
        svc._mutate_and_update_ext.assert_not_called()
        build.upgrade_async.assert_not_awaited()
    else:
        assert calls == ['enter', 'error', 'exit']
        build.upgrade_async.assert_awaited_once()
    svc._publish_service.update_publish_status_with_ext.assert_not_called()
    baas.list_devices_by_bot_uuid.assert_not_called()
    baas.post_bots_api.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['openclaw', 'other-engine'])
@pytest.mark.parametrize('error', [RuntimeError('provider failure'), RestartBackupBlocked('backup_failed')])
async def test_default_flow_propagates_provider_error_without_engine_failure_writes(engine, error):
    svc, ledger, _, build, record = _setup(engine)
    build.upgrade_async.side_effect = error
    with pytest.raises(type(error)) as caught:
        await svc.execute_restart(1, 'online', 'operator')
    assert caught.value is error
    assert record.status == PublishStatus.SUCCESS.value
    assert record.ext['restart'] == {'restarting': True}
    svc._publish_service.update_publish_status_with_ext.assert_not_called()
    assert ledger.get_latest_by_kind(1, 'restart', 'online').state == PublishOperationState.PENDING.value


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
async def test_coding_scope_does_not_turn_cancellation_into_a_backup_veto(engine):
    svc, ledger, _, build, record = _setup(engine)
    error = CancelledError()
    build.upgrade_async.side_effect = error
    with pytest.raises(CancelledError) as caught:
        await svc.execute_restart(1, 'online', 'operator')
    assert caught.value is error
    assert record.status == PublishStatus.SUCCESS.value
    assert record.ext['restart']['restarting'] is True
    assert 'precondition_error' not in record.ext['restart']
    svc._publish_service.update_publish_status_with_ext.assert_not_called()
    assert ledger.get_latest_by_kind(1, 'restart', 'online').state == PublishOperationState.PENDING.value
