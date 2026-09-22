"""Coding-only restart preconditions, receipt fencing, and sanitized logs."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding import restart_backup as backup
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
from agentclaw.community.core.bot_management.engines.default import DefaultProvisioningStrategy
from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext
from agentclaw.community.core.bot_management.engines.registry import prepare_instance_restart

OPERATION = 'a' * 32


def response(status, **values):
    return SimpleNamespace(exit_code=0, stdout=json.dumps(dict(
        version=1, operation_id=OPERATION, boot_id='instance-1', status=status) | values))


def prepare(execute):
    with patch.object(backup.time, 'sleep'):
        return backup.prepare_backup(execute=execute, operation_id=OPERATION,
                                     bot_id='bot-1', target_id='container-1')


@pytest.mark.parametrize('status', ['legacy', 'not_mounted'])
def test_confirmed_no_backup_returns_verifier(status, caplog):
    execute = Mock(return_value=response(status))
    verify = prepare(execute)
    assert execute.call_count == 1
    verify()
    assert execute.call_count == 2
    assert 'phase=prepared status=' + status in caplog.text
    assert 'phase=verify status=allowed' in caplog.text
    assert 'bot_id=bot-1 target_id=container-1 operation_id=' + OPERATION in caplog.text


def test_poll_and_short_verification_require_matching_receipt(caplog):
    committed = response('committed', backup={
        'status': 'success', 'generation_id': 'g1', 'operation_id': OPERATION})
    execute = Mock(side_effect=[response('running'), response('running'), committed, committed])
    verify = prepare(execute)
    verify()
    assert caplog.text.count('phase=wait status=running') == 1
    assert 'generation_id=g1' in caplog.text
    assert 'elapsed_ms=' in caplog.text


@pytest.mark.parametrize('result', [SimpleNamespace(exit_code=1, stdout='secret-output'),
    SimpleNamespace(exit_code=0, stdout='secret-output'), response('failed'),
    response('committed'), response('skipped')])
def test_failed_or_unknown_results_block_and_do_not_log_raw_output(result, caplog):
    with pytest.raises(RuntimeError):
        prepare(Mock(return_value=result))
    assert 'phase=prepare status=blocked' in caplog.text
    assert 'secret-output' not in caplog.text


@pytest.mark.parametrize('later', ['legacy', 'not_mounted'])
def test_runtime_disappearing_during_poll_blocks(later):
    with pytest.raises(RuntimeError):
        prepare(Mock(side_effect=[response('running'), response(later)]))


def test_new_container_cannot_use_previous_receipt():
    committed = response('committed', backup={
        'status': 'success', 'generation_id': 'g1', 'operation_id': OPERATION})
    changed = response('committed', boot_id='replacement', backup={
        'status': 'success', 'generation_id': 'g1', 'operation_id': OPERATION})
    verify = prepare(Mock(side_effect=[committed, changed]))
    with pytest.raises(RuntimeError, match='实例'):
        verify()


def test_timeout_never_allows_replacement(caplog):
    with patch.object(backup, 'DEADLINE_SECONDS', 0), pytest.raises(TimeoutError):
        prepare(Mock(return_value=response('running')))
    assert 'error_type=TimeoutError' in caplog.text


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
def test_binding_change_blocks_without_writing_bot_state(engine):
    device = Mock()
    device.get_device.return_value = {'device_id': 'sandbox-1'}
    repository = Mock()
    repository.get_by_id_and_owner.return_value = {'binding_id': 99}
    ctx = BotProvisioningContext(bot_id='bot', owner_id='owner', bot_type='personal', active_engine=engine)
    receipt_check = Mock()
    with patch.object(backup, 'prepare_backup', return_value=receipt_check):
        verify = AicodingProvisioningStrategy(engine).prepare_restart(
            ctx, binding_id=42, device_service=device, bot_repository=repository)
    with pytest.raises(RuntimeError, match='绑定'):
        verify()
    receipt_check.assert_not_called()
    repository.update_by_owner.assert_not_called()


def test_default_engine_is_noop():
    device = Mock()
    DefaultProvisioningStrategy().prepare_restart(None, binding_id=42, device_service=device)()
    device.get_device.assert_not_called()


@pytest.mark.asyncio
async def test_other_engine_instance_does_not_probe_or_lock():
    runtime = Mock()
    await prepare_instance_restart(bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': 'openclaw'},
                                   device_id='target', target_runtime=runtime)
    assert not runtime.mock_calls


@pytest.mark.asyncio
async def test_instance_targets_are_pinned_and_inventory_is_rechecked():
    runtime = Mock()
    runtime.get_bot.side_effect = [
        {'devices': [{'provider_device_id': 'physical-1', 'status': 'ACTIVE'}]},
        {'devices': [{'provider_device_id': 'physical-2', 'status': 'ACTIVE'}]},
    ]
    with patch.object(backup, 'prepare_backup', return_value=Mock()) as prepared:
        with pytest.raises(RuntimeError, match='清单变化'):
            await prepare_instance_restart(bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': 'aicoding'},
                                           device_id='caller-uuid', target_runtime=runtime)
    prepared.call_args.kwargs['execute']('probe')
    runtime.exec_command_on_bot.assert_called_once_with(
        bot_uuid='caller-uuid', paas_device_id='physical-1', cmd='probe')


@pytest.mark.parametrize('error', [PermissionError('denied'), OSError('I/O')])
def test_probe_access_errors_are_not_legacy(error):
    import os
    import shlex
    code = shlex.split(backup.command('start', OPERATION))[-1]
    with patch.object(os, 'lstat', side_effect=error), pytest.raises(type(error)):
        exec(compile(code, '<restart-probe>', 'exec'), {})


def test_only_confirmed_absence_is_legacy(capsys):
    import os
    import shlex
    code = shlex.split(backup.command('status', OPERATION))[-1]
    with patch.object(os, 'lstat', side_effect=FileNotFoundError()):
        exec(compile(code, '<restart-probe>', 'exec'), {})
    assert json.loads(capsys.readouterr().out)['status'] == 'legacy'
    with patch.object(os, 'lstat', side_effect=[FileNotFoundError(), SimpleNamespace()]), pytest.raises(RuntimeError):
        exec(compile(code, '<restart-probe>', 'exec'), {})


@pytest.mark.parametrize('phase', ['prepare', 'verify'])
@pytest.mark.parametrize('provider', ['arca', 'baas'])
def test_original_lock_and_binding_survive_backup_failure(phase, provider):
    from tests.community.core.bot_management.services.test_bot_service_restart_idempotency import (
        FakeRestartLockRepo, _make_service, _make_bot, _stateful_bot_repository, BotServiceError,
    )
    locks = FakeRestartLockRepo()
    bot = _make_bot(active_engine='aicoding', binding_id=42)
    repository, state = _stateful_bot_repository(bot)
    device = Mock()
    device.get_device.return_value = {'device_id': 'old-container', 'device_provider': provider, 'status': 'ACTIVE'}
    svc = _make_service(locks, bot_repository=repository, device_provider=device,
                        baas_service_provider=lambda: Mock())

    def prepare_check(**kwargs):
        assert not locks.rows  # No 25-minute wait inside the old 120-second lease.
        if phase == 'prepare':
            raise RuntimeError('backup failed')

        def verify():
            assert locks.rows  # Recheck receipt while holding the original lock.
            raise RuntimeError('backup failed')
        return verify

    with patch.object(backup, 'prepare_backup', side_effect=prepare_check), \
         patch.object(svc, 'stop_bot') as stop, patch.object(svc, 'start_bot') as start, \
         patch.object(svc, '_restart_bot_baas') as update:
        with pytest.raises(BotServiceError, match='backup failed'):
            svc.restart_bot(bot_id='bot001', user_id='user001')
    stop.assert_not_called()
    start.assert_not_called()
    update.assert_not_called()
    assert state['binding_id'] == 42 and state['status'] == 'ACTIVE'
    assert not locks.rows


def test_wait_logs_are_throttled_but_keep_progress(caplog):
    now = [0]
    committed = response('committed', backup={
        'status': 'success', 'generation_id': 'g1', 'operation_id': OPERATION})
    execute = Mock(side_effect=[response('running')] * 32 + [committed])
    with patch.object(backup.time, 'monotonic', side_effect=lambda: now[0]), \
         patch.object(backup.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
        backup.prepare_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='t')
    assert caplog.text.count('phase=wait status=running') == 2
    assert 'phase=prepared status=committed' in caplog.text
