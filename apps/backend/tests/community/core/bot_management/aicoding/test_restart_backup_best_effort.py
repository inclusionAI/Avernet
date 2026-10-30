"""Backup failures yield to restart; lifecycle guards remain mandatory."""
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import RestartBackupDependencies

from agentclaw.community.core.bot_management.engines.aicoding import restart_backup as backup
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext
from agentclaw.community.core.bot_management.engines.registry import prepare_instance_restart
from tests.community.core.bot_management.aicoding.test_restart_backup import OPERATION, response


def context(engine='aicoding'):
    return BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine=engine)


def committed(**changes):
    return response('committed', backup={
        'status': 'success', 'generation_id': 'g1', 'operation_id': OPERATION,
    }, **changes)


@pytest.mark.parametrize('failure', [
    ConnectionError('secret credential'), TimeoutError('secret credential'),
    PermissionError('secret credential'),
    response('failed'), response('committed'), response('unknown'),
    response('running', operation_id='b' * 32), response('running', boot_id=None),
    SimpleNamespace(exit_code=1, stdout='secret credential'),
    SimpleNamespace(exit_code=0, stdout='secret credential'), {}, None,
])
@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
def test_preparation_failures_continue_with_warning_not_fake_success(failure, engine, caplog):
    caplog.set_level(logging.INFO, logger=backup.logger.name)
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.return_value = [dict(provider_device_id='physical', status='ACTIVE')]
    if isinstance(failure, Exception):
        runtime.post_bots_api.side_effect = failure
    else:
        runtime.post_bots_api.return_value = failure
    check = AicodingProvisioningStrategy(engine)._prepare_restart(
        context(engine), device_id='target', restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION,
    )
    check()
    assert runtime.post_bots_api.call_count == 1  # Never retry start during verification.
    assert 'status=degraded action=continue_restart' in caplog.text
    assert 'data_loss_risk=true' in caplog.text
    assert 'status=committed' not in caplog.text
    assert 'secret credential' not in caplog.text


@pytest.mark.parametrize('inventory', [[], None, {}, [None], [{'status': 'ACTIVE'}],
                                       ConnectionError('secret inventory')])
def test_unavailable_inventory_continues_without_random_target(inventory, caplog):
    runtime = Mock()
    if isinstance(inventory, Exception):
        runtime.list_devices_by_bot_uuid.side_effect = inventory
    else:
        runtime.list_devices_by_bot_uuid.return_value = inventory
    verify = AicodingProvisioningStrategy('aicoding')._prepare_restart(
        context(), device_id='target', restart_dependencies=RestartBackupDependencies.for_instance(runtime),
    )
    verify()
    runtime.post_bots_api.assert_not_called()
    assert 'action=continue_restart' in caplog.text
    assert 'secret inventory' not in caplog.text
    expected_reason = ('transport_error' if isinstance(inventory, Exception)
                       else 'empty_inventory' if inventory == [] else 'invalid_inventory')
    degraded = [record.message for record in caplog.records if 'status=degraded' in record.message]
    assert len(degraded) == 2  # Resolve and under-lock verification use the same reason.
    assert all(f'reason={expected_reason} ' in message for message in degraded)


@pytest.mark.parametrize('failure', [ConnectionError('secret'), response('failed'),
                                    response('committed'), response('not_mounted'), committed(boot_id=None),
                                    SimpleNamespace(exit_code=0, stdout='bad')])
def test_receipt_recheck_failure_continues(failure, caplog):
    execute = Mock(side_effect=[committed(), failure])
    verify = backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='p')
    verify()
    assert 'phase=verify_receipt status=degraded' in caplog.text
    assert execute.call_count == 2


def test_backup_timeout_continues_without_restarting_backup(caplog):
    execute = Mock(return_value=response('running'))
    now = [0]
    with patch.object(backup.time, 'monotonic', side_effect=lambda: now[0]), \
         patch.object(backup.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
        verify = backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='p')
    calls = execute.call_count
    verify()
    assert execute.call_count == calls
    assert now[0] == backup.DEADLINE_SECONDS == 1500  # Archive's 900s budget is unchanged.
    assert 'reason=timeout' in caplog.text and 'action=continue_restart' in caplog.text


@pytest.mark.parametrize('phase', ['prepare', 'verify'])
def test_observed_instance_change_degrades(phase, caplog):
    sequence = [response('running'), committed(boot_id='new')] if phase == 'prepare' else [committed(), committed(boot_id='new')]
    with patch.object(backup.time, 'sleep'):
        backup._attempt_backup(execute=Mock(side_effect=sequence), operation_id=OPERATION, bot_id='b', target_id='p')()
    reason = 'instance_changed' if phase == 'prepare' else 'receipt_stale'
    assert f'reason={reason}' in caplog.text
    assert 'action=continue_restart' in caplog.text


def test_inventory_change_continues_after_failed_backup(caplog):
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.side_effect = [
        [{'provider_device_id': 'old', 'status': 'ACTIVE'}],
        [{'provider_device_id': 'new', 'status': 'ACTIVE'}],
    ]
    runtime.post_bots_api.side_effect = ConnectionError('unreachable')
    verify = AicodingProvisioningStrategy('aicoding')._prepare_restart(context(), device_id='target', restart_dependencies=RestartBackupDependencies.for_instance(runtime))
    verify()
    assert 'reason=target_changed' in caplog.text
    assert runtime.post_bots_api.call_count == 1


def test_verification_inventory_unavailable_does_not_block():
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.side_effect = [
        [{'provider_device_id': 'old', 'status': 'ACTIVE'}], ConnectionError('unavailable'),
    ]
    runtime.post_bots_api.return_value = committed()
    verify = AicodingProvisioningStrategy('aicoding')._prepare_restart(
        context(), device_id='target', restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION,
    )
    verify()
    assert runtime.post_bots_api.call_count == 2


def test_each_replica_still_gets_backup_attempt_after_one_fails():
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.return_value = [
        {'provider_device_id': 'p1', 'status': 'ACTIVE'}, {'provider_device_id': 'p2', 'status': 'ACTIVE'},
    ]
    runtime.post_bots_api.side_effect = [ConnectionError('down'), committed(), committed()]
    verify = AicodingProvisioningStrategy('aicoding')._prepare_restart(
        context(), device_id='target', restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION,
    )
    verify()
    assert [c.kwargs['path'] for c in runtime.post_bots_api.call_args_list] == [
        '/api/v1/paas/devices/p1/commands', '/api/v1/paas/devices/p2/commands', '/api/v1/paas/devices/p2/commands',
    ]


def test_binding_read_errors_are_not_swallowed_as_backup_warnings():
    device = Mock()
    device.get_device.side_effect = RuntimeError('database unavailable')
    with pytest.raises(RuntimeError, match='database unavailable'):
        AicodingProvisioningStrategy('aicoding').prepare_restart(context(), binding_id=7, device_service=device)


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
async def test_instance_restart_contract_continues_on_command_failure(engine):
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.return_value = [{'provider_device_id': 'p', 'status': 'ACTIVE'}]
    runtime.post_bots_api.side_effect = ConnectionError('down')
    await prepare_instance_restart(bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': engine},
                                   device_id='target', target_runtime=runtime)
    runtime.post_bots_api.assert_called_once()


def test_task_cancellation_is_not_converted_to_backup_warning():
    import asyncio
    with pytest.raises(asyncio.CancelledError):
        backup._attempt_backup(execute=Mock(side_effect=asyncio.CancelledError()),
                               operation_id=OPERATION, bot_id='b', target_id='p')


def test_durable_budget_is_shared_across_replicas_and_redelivery(caplog):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import current_restart

    now = [1498]
    context_reset_handle = current_restart.set(SimpleNamespace(
        payload={'started_at': 0}, state=Mock(read=Mock(return_value={'ext': {}}))))
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.return_value = [
        {'provider_device_id': p, 'status': 'ACTIVE'} for p in ('p1', 'p2')]
    runtime.post_bots_api.return_value = response('running')
    try:
        with patch.object(backup.time, 'time', side_effect=lambda: now[0]), \
             patch.object(backup.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(backup.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
            for _ in range(2):  # A later delivery cannot restart the budget either.
                AicodingProvisioningStrategy('aicoding')._prepare_restart(
                    context(), device_id='target', restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION)()
        assert now[0] == 1500
        runtime.post_bots_api.assert_called_once()
        assert runtime.post_bots_api.call_args.kwargs['path'].endswith('/p1/commands')
        assert 'reason=timeout' in caplog.text
    finally:
        current_restart.reset(context_reset_handle)


def test_late_valid_inventory_becomes_baseline_without_starting_backup(caplog):
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.side_effect = [
        ConnectionError('initial lookup'),
        [{'provider_device_id': 'old', 'status': 'ACTIVE'}],
        ConnectionError('temporary lookup'),
        [{'provider_device_id': 'new', 'status': 'ACTIVE'}],
    ]
    verify = AicodingProvisioningStrategy('aicoding')._prepare_restart(
        context(), device_id='target', restart_dependencies=RestartBackupDependencies.for_instance(runtime))
    verify()
    verify()
    verify()
    assert 'reason=target_changed' in caplog.text
    runtime.post_bots_api.assert_not_called()


@pytest.fixture(autouse=True)
def nonfastdisk_compatibility_scope(monkeypatch):
    """Legacy backup/fence tests; real detection and strict policy have their own suite."""
    from agentclaw.community.core.bot_management.engines.aicoding import restart_backup
    monkeypatch.setattr(restart_backup, "_detect_fastdisk", lambda *args, **kwargs: False)
