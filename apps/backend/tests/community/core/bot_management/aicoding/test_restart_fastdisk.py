"""Only backend-confirmed fastdisk backups may veto a coding restart."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding import restart_backup as backup
from agentclaw.community.core.bot_management.engines.aicoding.artifact_transfer import detection_command
from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import RestartBackupDependencies
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext
from agentclaw.community.core.bot_management.engines.registry import prepare_instance_restart
from tests.community.core.bot_management.aicoding.test_restart_backup import OPERATION, response


def detection(status='run'):
    return SimpleNamespace(exit_code=0, stdout=json.dumps({
        'status': status, 'storage': '/opt/.aicoding', 'home': '/home/admin/.aicoding',
    }))


def committed(**changes):
    event = dict(status='success', generation_id='g1', operation_id=OPERATION)
    event.update(changes)
    return response('committed', backup=event)


def executor(results, detected=None):
    """Exercise the real detector/parser; never mock fastdisk classification."""
    results = iter(results)
    detector = detection_command()

    def execute(cmd):
        value = (detection() if detected is None else detected) if cmd == detector else next(results)
        if isinstance(value, Exception):
            raise value
        return value

    return Mock(side_effect=execute)


def attempt(execute):
    with patch.object(backup.time, 'sleep'):
        return backup._attempt_backup(execute=execute, operation_id=OPERATION,
                                      bot_id='b', target_id='physical')


@pytest.mark.parametrize('failure', [
    response('failed'), response('partial_success'), response('legacy'), response('not_mounted'),
    committed(completeness='degraded'), committed(status='partial_success'),
    committed(generation_id=''), committed(operation_id='b' * 32),
    SimpleNamespace(exit_code=1, stdout='private output'),
    SimpleNamespace(exit_code=0, stdout='invalid json'),
    ConnectionError('private credential'), TimeoutError('private credential'),
])
def test_confirmed_fastdisk_failure_blocks_without_leaking_output(failure, caplog):
    execute = executor([failure])
    with pytest.raises(backup.RestartBackupError) as raised:
        attempt(execute)
    assert 'private' not in str(raised.value)
    assert 'private' not in caplog.text
    assert execute.call_args_list[0].args == (detection_command(),)
    assert 'action=continue_restart' not in caplog.text


@pytest.mark.parametrize('fields', [{}, {'completeness': 'success'}])
def test_confirmed_fastdisk_success_reuses_existing_start_status_protocol(fields):
    execute = executor([response('running'), committed(**fields), committed(**fields)])
    check = attempt(execute)
    check()
    assert execute.call_args_list[0].args == (detection_command(),)
    assert execute.call_count == 4


@pytest.mark.parametrize('failure', [response('failed'), response('legacy'), response('not_mounted'),
                                    response('partial_success'), committed(completeness='degraded'),
                                    committed(generation_id='new'), ConnectionError('private')])
def test_receipt_failure_after_confirmed_fastdisk_never_downgrades(failure):
    execute = executor([committed(), failure])
    check = attempt(execute)
    with pytest.raises(backup.RestartBackupError):
        check()


@pytest.mark.parametrize('detected', [
    detection('skip_no_fastdisk'), detection('unexpected'), ConnectionError('secret detector'),
    SimpleNamespace(exit_code=2, stdout='private detection'),
    SimpleNamespace(exit_code=0, stdout='not-json'),
    SimpleNamespace(exit_code=0, stdout=json.dumps({'status': 'run', 'storage': '../unsafe', 'home': '/home'})),
])
def test_detection_failure_or_nonfastdisk_retains_best_effort(detected, caplog):
    execute = executor([response('failed')], detected=detected)
    attempt(execute)()
    assert execute.call_args_list[0].args == (detection_command(),)
    assert execute.call_count == 2
    assert 'action=continue_restart' in caplog.text
    assert 'private' not in caplog.text and 'secret detector' not in caplog.text
    assert 'AICODING_RESTART_FAILED' not in caplog.text


def test_confirmed_fastdisk_poll_timeout_blocks():
    execute = executor([response('running')] * 3)
    now = [0]
    with patch.object(backup, 'DEADLINE_SECONDS', 2), \
         patch.object(backup.time, 'monotonic', side_effect=lambda: now[0]), \
         patch.object(backup.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
        with pytest.raises(backup.RestartBackupError) as raised:
            backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='p')
    assert raised.value.reason == 'timeout'


def runtime_with(execute):
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.return_value = [{'provider_device_id': 'physical', 'status': 'ACTIVE'}]
    runtime.post_bots_api.side_effect = lambda **kw: execute(kw['payload']['cmd'])
    return runtime


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
def test_nested_baas_compatibility_catch_does_not_swallow_strict_failure(engine):
    runtime = runtime_with(executor([response('failed')]))
    devices = Mock()
    devices.get_device.return_value = dict(device_id='old', device_provider='baas')
    ctx = BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine=engine)
    with pytest.raises(backup.RestartBackupError):
        AicodingProvisioningStrategy(engine)._prepare_restart(
            ctx, binding_id=7, device_service=devices, bot_repository=Mock(),
            restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION,
        )


@pytest.mark.parametrize('inventory', [ConnectionError('private inventory'),
    [{'provider_device_id': 'different', 'status': 'ACTIVE'}], []])
def test_inventory_recheck_cannot_bypass_a_confirmed_fastdisk(inventory):
    execute = executor([committed(), committed()])
    runtime = runtime_with(execute)
    runtime.list_devices_by_bot_uuid.side_effect = [runtime.list_devices_by_bot_uuid.return_value, inventory]
    ctx = BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding')
    check = AicodingProvisioningStrategy('aicoding')._prepare_restart(
        ctx, device_id='old', restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION)
    with pytest.raises(backup.RestartBackupError):
        check()


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
async def test_instance_restart_uses_the_same_strict_strategy(engine):
    runtime = runtime_with(executor([response('failed')]))
    with pytest.raises(backup.RestartBackupError):
        await prepare_instance_restart(bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': engine},
                                       device_id='old', target_runtime=runtime, operation_id=OPERATION)


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['openclaw', 'moltis', 'hermes'])
async def test_non_coding_instance_never_detects_or_backs_up(engine, caplog):
    runtime = Mock()
    await prepare_instance_restart(bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': engine},
                                   device_id='old', target_runtime=runtime)
    assert not runtime.mock_calls
    assert 'AICODING_RESTART_FAILED' not in caplog.text

# Actual BotService + durable handler, only external platform I/O is replaced.
from tests.community.core.bot_management.aicoding.test_restart_service import lifecycle as lifecycle  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['baas', 'arca'])
@pytest.mark.parametrize('outcome', ['failed', 'partial_success', 'legacy', 'not_mounted'])
async def test_durable_failure_uses_existing_ui_fields_without_destroying_old_target(lifecycle, provider, outcome, caplog, monkeypatch):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_arca import ArcaRestartTransport
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import TASK_TYPE, task_key, journal
    from agentclaw.community.core.task_queue.types import Fail

    monkeypatch.setenv('SERVER_ENV', 'pre')
    s = lifecycle
    s.binding.update(device_provider=provider, device_props={'sandbox_id': 'old-sandbox@8'})
    s.service._resolve_restart_target_provider = Mock(return_value=provider)
    s.service.stop_bot = Mock()
    s.service.start_bot = Mock()
    s.platform.list_devices_by_bot_uuid.return_value = [{'provider_device_id': 'ARCA-SANDBOX-old@8', 'status': 'ACTIVE'}]
    s.service._restart_backup_dependencies = RestartBackupDependencies(
        baas_runtime=lambda: s.platform,
        arca_transport=lambda: ArcaRestartTransport(lambda **kw: execute(kw['shell_cmd'])),
    )
    await s.service.restart_bot_async(bot_id='b', user_id='o')
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key('b', 'o'))
    execute = executor([response(outcome, operation_id=task.payload['operation_id'])])
    s.platform.post_bots_api.side_effect = lambda **kw: execute(kw['payload']['cmd'])
    assert isinstance(s.handler.handle(task.payload), Fail)
    assert isinstance(s.handler.handle(task.payload), Fail)  # Terminal redelivery does no work.
    assert execute.call_count == 2
    s.service.stop_bot.assert_not_called()
    s.service.start_bot.assert_not_called()
    assert not s.queue.provider_tasks
    assert s.repo.bot['binding_id'] == 7
    assert s.repo.bot['status'] == 'FAILED'
    assert s.repo.bot['ext']['start_status'] == 'FAILED'
    assert '快盘' in s.repo.bot['ext']['start_message']
    assert '未执行销毁或替换' in s.repo.bot['ext']['start_message']
    assert journal(s.repo.bot)['phase'] == 'FAILED'
    assert s.repo.bot['ext']['unrelated'] == {'keep': True}
    failures = [r.getMessage() for r in caplog.records if '[AICODING_RESTART_FAILED]' in r.getMessage()]
    summary = next(line for line in failures if 'phase=task_failed' in line)
    arca_id = 'old-sandbox@8' if provider == 'arca' else 'ARCA-SANDBOX-old@8'
    assert f'bot_id=b arca_id={arca_id} ' in summary
    assert f"operation_id={task.payload['operation_id']}" in summary
    assert f'helper_status={outcome}' in summary
    assert 'env=pre owner_id=o ' in summary


def test_redelivery_does_not_downgrade_previous_fastdisk_confirmation():
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import current_restart

    state = Mock()
    state.read.return_value = {'ext': {'coding_restart': {'fastdisk_targets': ['physical']}}}
    token = current_restart.set(SimpleNamespace(state=state, payload={'started_at': backup.time.time()}))
    execute = executor([response('failed')], detected=ConnectionError('unreachable'))
    try:
        with pytest.raises(backup.RestartBackupError):
            attempt(execute)
    finally:
        current_restart.reset(token)
    assert execute.call_count == 1  # Reuse the persisted positive result, never downgrade it.
    assert execute.call_args.args != (detection_command(),)


def test_redelivery_inventory_failure_keeps_confirmed_fastdisk_guard():
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import current_restart

    state = Mock()
    state.read.return_value = {'ext': {'coding_restart': {'fastdisk_targets': ['physical']}}}
    token = current_restart.set(SimpleNamespace(state=state, payload={'started_at': backup.time.time()}))
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.side_effect = ConnectionError('unknown inventory')
    try:
        with pytest.raises(backup.RestartBackupError):
            AicodingProvisioningStrategy('aicoding')._prepare_restart(
                BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'),
                device_id='old', restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION)
    finally:
        current_restart.reset(token)
    runtime.post_bots_api.assert_not_called()


def test_positive_detection_write_failure_blocks_before_start():
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import current_restart

    state = Mock()
    state.read.return_value = {'ext': {}}
    state.update.side_effect = RuntimeError('database unavailable')
    token = current_restart.set(SimpleNamespace(state=state, payload={'started_at': backup.time.time(), 'device_id': 'old', 'provider': 'baas'}))
    runtime = runtime_with(executor([committed()]))
    devices = Mock()
    devices.get_device.return_value = dict(device_id='old', device_provider='baas')
    try:
        with pytest.raises(backup.RestartBackupBlocked, match='结果无法确认'):
            AicodingProvisioningStrategy('aicoding')._prepare_restart(
                BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'),
                binding_id=7, device_service=devices, bot_repository=Mock(),
                restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION)
    finally:
        current_restart.reset(token)
    runtime.post_bots_api.assert_called_once()  # Detection only; no unjournaled backup.


def test_redelivery_dependency_failure_cannot_bypass_positive_confirmation():
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import current_restart

    state = Mock()
    state.read.return_value = {'ext': {'coding_restart': {'fastdisk_targets': ['physical']}}}
    token = current_restart.set(SimpleNamespace(state=state, payload={'device_id': 'old', 'provider': 'baas'}))
    devices = Mock()
    devices.get_device.return_value = dict(device_id='old', device_provider='baas')
    try:
        with pytest.raises(backup.RestartBackupBlocked):
            AicodingProvisioningStrategy('aicoding')._prepare_restart(
                BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'),
                binding_id=7, device_service=devices,
                restart_dependencies=RestartBackupDependencies(baas_runtime=Mock(side_effect=RuntimeError('DI failure'))),
                operation_id=OPERATION)
    finally:
        current_restart.reset(token)


def test_mixed_replicas_cannot_hide_failed_fastdisk_behind_nonfastdisk():
    plain = executor([response('failed')], detected=detection('skip_no_fastdisk'))
    fast = executor([response('partial_success')])
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.return_value = [
        {'provider_device_id': p, 'status': 'ACTIVE'} for p in ('plain', 'fast')]
    runtime.post_bots_api.side_effect = lambda **kw: (
        plain if '/plain/' in kw['path'] else fast)(kw['payload']['cmd'])
    with pytest.raises(backup.RestartBackupBlocked, match='不完整'):
        AicodingProvisioningStrategy('aicoding')._prepare_restart(
            BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'),
            device_id='old', restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION)
    assert plain.call_count == fast.call_count == 2


def test_confirmed_fastdisk_uses_five_minute_budget_not_legacy_25_minutes():
    now = [0]
    execute = executor([response('running')] * 151)
    with patch.object(backup.time, 'monotonic', side_effect=lambda: now[0]), \
         patch.object(backup.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
        with pytest.raises(backup.RestartBackupBlocked) as raised:
            backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='p')
    assert raised.value.reason == 'timeout'
    assert now[0] == backup.FASTDISK_TIMEOUT == 300


@pytest.mark.asyncio
@pytest.mark.parametrize('final_failure', [False, True])
async def test_real_baas_final_receipt_gates_update_submission(lifecycle, final_failure):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import TASK_TYPE, task_key
    from agentclaw.community.core.task_queue.types import Fail, Reschedule

    s = lifecycle
    s.service._restart_backup_dependencies = RestartBackupDependencies.for_instance(s.platform)
    s.platform.list_devices_by_bot_uuid.return_value = [{'provider_device_id': 'physical', 'status': 'ACTIVE'}]
    await s.service.restart_bot_async(bot_id='b', user_id='o')
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key('b', 'o'))
    op = task.payload['operation_id']
    receipt = response('committed', operation_id=op, backup={
        'status': 'success', 'generation_id': 'g1', 'operation_id': op, 'completeness': 'success'})
    execute = executor([receipt, receipt, response('partial_success', operation_id=op) if final_failure else receipt])
    updates = []

    def post(**kw):
        if kw['path'].endswith('/commands'):
            return execute(kw['payload']['cmd'])
        updates.append(kw)
        return {'publish_id': 12}

    s.platform.post_bots_api.side_effect = post
    outcome = s.handler.handle(task.payload)
    assert execute.call_count == 4  # Detect, start, under-lock verify, final pre-POST verify.
    if final_failure:
        assert isinstance(outcome, Fail)
        assert not updates
        assert '快盘备份不完整' in s.repo.bot['ext']['start_message']
        assert '提交前准备失败' not in s.repo.bot['ext']['start_message']
        assert s.repo.bot['binding_id'] == 7
    else:
        assert isinstance(outcome, Reschedule)
        assert len(updates) == 1
