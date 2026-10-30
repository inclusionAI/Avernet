"""Arca uses the old binding's sandbox identity, never BaaS inventory."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import RestartBackupDependencies

from agentclaw.community.core.bot_management.engines.aicoding import restart_backup as backup
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext
from agentclaw.community.core.devices.errors import InvalidDeviceStatusError
from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter
from agentclaw.community.core.bot_management.engines.aicoding.restart_arca import (
    ArcaRestartTransport, BoundArcaRestartExecutor, RestartBackupTargetChanged,
)
from tests.community.core.bot_management.aicoding.test_restart_backup import OPERATION, response
from tests.community.core.devices.services.test_device_service import _make_service, _make_record


@pytest.fixture
def target():
    record = _make_record(id=42, device_provider='arca', status='ACTIVE')
    record.device_props = {'sandbox_id': 'old-sandbox@8'}
    repo = Mock()
    repo.get_by_id.return_value = repo.get_by_device_id.return_value = record
    provider = _make_service(repo=repo)
    committed = response('committed', backup={
        'status': 'success', 'generation_id': 'g1', 'operation_id': OPERATION,
    })
    provider._exec_shell_new = Mock(return_value=committed)
    router = DeviceServiceRouter(
        repository=repo, bot_query=Mock(), providers={'arca': provider}, default_provider_key='arca',
    )
    bot_repo = Mock()
    bot_repo.get_by_id_and_owner.return_value = {'binding_id': 42}
    runtime = Mock(side_effect=AssertionError('Arca must not resolve BaaS'))
    ctx = BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding')
    return SimpleNamespace(record=record, repo=repo, provider=provider, router=router,
                           bot_repo=bot_repo, runtime=runtime, ctx=ctx)


def prepare(s):
    return AicodingProvisioningStrategy(s.ctx.active_engine).prepare_restart(
        s.ctx, binding_id=42, device_service_provider=lambda: s.router,
        bot_repository=s.bot_repo, operation_id=OPERATION,
        restart_dependencies=RestartBackupDependencies(
            baas_runtime=s.runtime,
            arca_transport=lambda: ArcaRestartTransport(s.provider._exec_shell_new),
        ),
    )


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.parametrize('status', ['ACTIVE', 'PENDING', 'FAILED', 'STOPPED'])
def test_all_releasable_states_backup_the_exact_old_sandbox(target, engine, status):
    s = target
    s.ctx = replace(s.ctx, active_engine=engine)
    s.record.status = status
    verify = prepare(s)
    verify()
    assert s.provider._exec_shell_new.call_count == 2
    for call in s.provider._exec_shell_new.call_args_list:
        assert call.kwargs['device'].device_id == s.record.device_id
        assert call.kwargs['device'].device_props == {'sandbox_id': 'old-sandbox@8'}
        assert OPERATION in call.kwargs['shell_cmd']
    s.runtime.assert_not_called()
    s.repo.release_binding.assert_not_called()
    s.bot_repo.update_by_owner.assert_not_called()
    if status in {'FAILED', 'STOPPED'}:
        with pytest.raises(InvalidDeviceStatusError):
            s.router.exec_shell_new(s.record.device_id, 'echo ordinary-command')
        assert s.provider._exec_shell_new.call_count == 2


@pytest.mark.parametrize('failure', ['transport', 'invalid', 'timeout', 'failed', 'legacy', 'not_mounted'])
def test_backup_only_failure_or_legacy_does_not_block_restart(target, failure, caplog):
    s = target
    if failure == 'transport':
        s.provider._exec_shell_new.side_effect = ConnectionError('secret upstream text')
    elif failure == 'invalid':
        s.provider._exec_shell_new.return_value = SimpleNamespace(exit_code=1, stdout='secret output')
    elif failure == 'timeout':
        s.provider._exec_shell_new.side_effect = TimeoutError('secret timeout')
    else:
        s.provider._exec_shell_new.return_value = response(failure)
    verify = prepare(s)
    verify()
    if failure not in {'legacy', 'not_mounted'}:
        assert 'action=continue_restart' in caplog.text
        assert 'data_loss_risk=true' in caplog.text
    assert 'secret' not in caplog.text


@pytest.mark.parametrize('change', ['sandbox', 'device', 'provider', 'released', 'missing', 'database'])
@pytest.mark.parametrize('backup_failed', [False, True])
def test_target_or_database_change_blocks_even_after_degraded_backup(target, change, backup_failed):
    s = target
    if backup_failed:
        s.provider._exec_shell_new.side_effect = TimeoutError()
    verify = prepare(s)
    before = s.provider._exec_shell_new.call_count
    if change == 'sandbox':
        s.record.device_props['sandbox_id'] = 'replacement@1'
    elif change == 'device':
        s.record.device_id = 'replacement'
    elif change == 'provider':
        s.record.device_provider = 'baas'
    elif change == 'released':
        s.record.status = 'RELEASED'
    elif change == 'missing':
        s.repo.get_by_id.return_value = None
    else:
        s.repo.get_by_id.side_effect = RuntimeError('database unavailable')
    with pytest.raises(RestartBackupTargetChanged):
        verify()
    assert s.provider._exec_shell_new.call_count == before


def test_target_change_during_poll_is_not_swallowed_as_best_effort(target):
    s = target

    def execute(**_):
        s.record.device_props['sandbox_id'] = 'replacement'
        return response('running')

    s.provider._exec_shell_new.side_effect = execute
    with patch.object(backup.time, 'sleep'), pytest.raises(RestartBackupTargetChanged):
        prepare(s)
    s.provider._exec_shell_new.assert_called_once()


def test_missing_transport_never_falls_back_to_ordinary_exec(target):
    s = target
    with pytest.raises(RestartBackupTargetChanged):
        BoundArcaRestartExecutor(
            bindings=s.router, transport=ArcaRestartTransport(), binding_id=42,
            device_id=s.record.device_id, sandbox_id='old-sandbox@8',
        )
    s.provider._exec_shell_new.assert_not_called()


def test_executor_keeps_snapshot_when_props_are_mutated(target):
    s = target
    executor = BoundArcaRestartExecutor(
        bindings=s.router, transport=ArcaRestartTransport(s.provider._exec_shell_new), binding_id=42, device_id=s.record.device_id, sandbox_id='old-sandbox@8',
    )
    original = deepcopy(s.record.device_props)
    s.record.device_props['sandbox_id'] = 'new-sandbox'
    with pytest.raises(RestartBackupTargetChanged):
        executor.execute(backup.command('start', OPERATION))
    s.record.device_props = original
    executor.execute(backup.command('start', OPERATION))
    s.provider._exec_shell_new.assert_called_once()


def test_missing_sandbox_is_degraded_but_later_target_appearance_blocks(target, caplog):
    s = target
    s.record.device_props = {}
    verify = prepare(s)
    verify()
    assert 'reason=missing_sandbox' in caplog.text
    s.provider._exec_shell_new.assert_not_called()
    s.record.device_props['sandbox_id'] = 'new-sandbox'
    with pytest.raises(RestartBackupTargetChanged):
        verify()
    s.provider._exec_shell_new.assert_not_called()


def test_running_backup_polls_before_final_verification(target):
    s = target
    committed = s.provider._exec_shell_new.return_value
    s.provider._exec_shell_new.side_effect = [response('running'), committed, committed]
    with patch.object(backup.time, 'sleep') as sleep:
        verify = prepare(s)
    assert s.provider._exec_shell_new.call_count == 2
    sleep.assert_called_once_with(backup.POLL_SECONDS)
    verify()
    assert s.provider._exec_shell_new.call_count == 3
    for call in s.provider._exec_shell_new.call_args_list:
        assert call.kwargs['device'].device_props == {'sandbox_id': 'old-sandbox@8'}
        assert OPERATION in call.kwargs['shell_cmd']
    s.runtime.assert_not_called()


@pytest.fixture(autouse=True)
def nonfastdisk_compatibility_scope(monkeypatch):
    """Legacy backup/fence tests; real detection and strict policy have their own suite."""
    from agentclaw.community.core.bot_management.engines.aicoding import restart_backup
    monkeypatch.setattr(restart_backup, "_detect_fastdisk", lambda *args, **kwargs: False)
