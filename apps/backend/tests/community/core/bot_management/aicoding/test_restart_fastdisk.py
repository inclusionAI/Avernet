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
from tests.community.core.bot_management.aicoding.test_restart_backup import OPERATION, partial_response, response


def strict_strategy(engine='aicoding'):
    config = Mock()
    config.get_value.return_value = True
    return AicodingProvisioningStrategy(engine, common_config_provider=lambda: config)


@pytest.fixture
def enabled_guard(monkeypatch):
    from agentclaw.community.core.bot_management.engines.registry import get_engine_provisioning_registry

    registry = get_engine_provisioning_registry()
    for engine in ('aicoding', 'claude_code'):
        config = Mock()
        config.get_value.return_value = True
        monkeypatch.setattr(registry.resolve(engine), '_common_config_provider', lambda config=config: config)


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
                                      bot_id='b', target_id='physical', blocking_enabled=lambda: True)


@pytest.mark.parametrize('failure', [
    response('failed'), response('partial_success'), response('legacy'), response('not_mounted'),
    committed(completeness='failed'), committed(status='partial_success'),
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
    with patch('agentclaw.community.core.bot_management.engines.aicoding.restart_state.record_nonblocking_backup_warning') as warn:
        attempt(execute)()
    warn.assert_not_called()
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
            backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='p', blocking_enabled=lambda: True)
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
        strict_strategy(engine)._prepare_restart(
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
    check = strict_strategy('aicoding')._prepare_restart(
        ctx, device_id='old', restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION)
    with pytest.raises(backup.RestartBackupError):
        check()


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
async def test_instance_restart_uses_the_same_strict_strategy(engine, enabled_guard):
    runtime = runtime_with(executor([response('failed')]))
    with pytest.raises(backup.RestartBackupError):
        await prepare_instance_restart(bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': engine},
                                       device_id='old', target_runtime=runtime, operation_id=OPERATION)


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['openclaw', 'moltis', 'hermes'])
async def test_non_coding_instance_never_detects_or_backs_up(engine, caplog, monkeypatch):
    from agentclaw.community import di

    injector = Mock(side_effect=AssertionError('non-coding must not resolve config'))
    monkeypatch.setattr(di, 'get_app_injector', injector)
    runtime = Mock()
    await prepare_instance_restart(bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': engine},
                                   device_id='old', target_runtime=runtime)
    assert not runtime.mock_calls
    injector.assert_not_called()
    assert 'AICODING_RESTART_FAILED' not in caplog.text

# Actual BotService + durable handler, only external platform I/O is replaced.
from tests.community.core.bot_management.aicoding.test_restart_service import lifecycle as lifecycle  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['baas', 'arca'])
@pytest.mark.parametrize('outcome', ['failed', 'legacy', 'not_mounted'])
async def test_durable_failure_uses_existing_ui_fields_without_destroying_old_target(lifecycle, provider, outcome, caplog, monkeypatch, enabled_guard):
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
    assert s.repo.bot['status'] == 'ACTIVE'
    assert s.repo.bot['ext'].get('start_status') != 'FAILED'
    assert '快盘' in journal(s.repo.bot)['error_message']
    assert '未执行销毁或替换' in journal(s.repo.bot)['error_message']
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
            strict_strategy('aicoding')._prepare_restart(
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
            strict_strategy('aicoding')._prepare_restart(
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
            strict_strategy('aicoding')._prepare_restart(
                BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'),
                binding_id=7, device_service=devices,
                restart_dependencies=RestartBackupDependencies(baas_runtime=Mock(side_effect=RuntimeError('DI failure'))),
                operation_id=OPERATION)
    finally:
        current_restart.reset(token)


def test_mixed_replicas_cannot_hide_failed_fastdisk_behind_nonfastdisk():
    plain = executor([response('failed')], detected=detection('skip_no_fastdisk'))
    fast = executor([response('failed')])
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.return_value = [
        {'provider_device_id': p, 'status': 'ACTIVE'} for p in ('plain', 'fast')]
    runtime.post_bots_api.side_effect = lambda **kw: (
        plain if '/plain/' in kw['path'] else fast)(kw['payload']['cmd'])
    with pytest.raises(backup.RestartBackupBlocked, match='快盘备份失败'):
        strict_strategy('aicoding')._prepare_restart(
            BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'),
            device_id='old', restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION)
    assert plain.call_count == fast.call_count == 2


def test_confirmed_fastdisk_uses_five_minute_budget_not_legacy_25_minutes():
    now = [0]
    execute = executor([response('running')] * 151)
    with patch.object(backup.time, 'monotonic', side_effect=lambda: now[0]), \
         patch.object(backup.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
        with pytest.raises(backup.RestartBackupBlocked) as raised:
            backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='p', blocking_enabled=lambda: True)
    assert raised.value.reason == 'timeout'
    assert now[0] == backup.FASTDISK_TIMEOUT == 300


@pytest.mark.asyncio
@pytest.mark.parametrize('final_failure', [False, True])
async def test_real_baas_final_receipt_gates_update_submission(lifecycle, final_failure, enabled_guard):
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
    execute = executor([receipt, receipt, response('failed', operation_id=op) if final_failure else receipt])
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
        assert '快盘备份失败' in s.repo.bot['ext']['coding_restart']['error_message']
        assert '提交前准备失败' not in s.repo.bot['ext']['coding_restart']['error_message']
        assert s.repo.bot['binding_id'] == 7
    else:
        assert isinstance(outcome, Reschedule)
        assert len(updates) == 1


@pytest.mark.parametrize('value, expected', [
    (True, True), (False, False), (None, False), ('true', False), (1, False), ({}, False),
])
def test_restart_config_requires_explicit_boolean_true_to_block(value, expected, monkeypatch):
    monkeypatch.setenv('SERVER_ENV', 'pre')
    config = Mock()
    config.get_value.return_value = value
    strategy = AicodingProvisioningStrategy('aicoding', common_config_provider=lambda: config)
    assert strategy._fastdisk_backup_blocking() is expected
    config.get_value.assert_called_once_with(
        business_code='aicoding', param_code='fastdisk_fail_block',
        env='pre', default=False, only_enabled=True,
    )


def test_restart_blocking_config_read_failure_keeps_nonblocking(caplog):
    strategy = AicodingProvisioningStrategy(
        'aicoding', common_config_provider=Mock(side_effect=RuntimeError('private credential')),
    )
    assert strategy._fastdisk_backup_blocking() is False
    assert 'private credential' not in caplog.text
    assert 'config_unavailable' in caplog.text


@pytest.mark.parametrize('failure', [response('failed'), response('partial_success'),
                                    response('legacy'), committed(completeness='failed')])
def test_disabled_guard_still_backs_up_and_warns_without_failure_alarm(failure, caplog):
    execute = executor([failure])
    check = backup._attempt_backup(
        execute=execute, operation_id=OPERATION, bot_id='b', target_id='ARCA-SANDBOX-p@8',
        blocking_enabled=lambda: False, owner_id='o',
    )
    check()
    assert check.required is False
    assert execute.call_count == 2
    assert 'action=continue_restart' in caplog.text
    assert 'arca_id=ARCA-SANDBOX-p@8' in caplog.text
    assert 'AICODING_RESTART_FAILED' not in caplog.text


def test_disabled_guard_keeps_five_minute_fastdisk_budget(caplog):
    now = [0]
    execute = executor([response('running')] * 151)
    with patch.object(backup.time, 'monotonic', side_effect=lambda: now[0]), \
         patch.object(backup.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
        backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='p',
                               blocking_enabled=lambda: False)()
    assert now[0] == backup.FASTDISK_TIMEOUT == 300
    assert 'AICODING_RESTART_FAILED' not in caplog.text


def test_disabled_guard_keeps_final_receipt_best_effort(caplog):
    execute = executor([committed(), response('failed')])
    with patch('agentclaw.community.core.bot_management.engines.aicoding.restart_state.record_nonblocking_backup_warning') as warn:
        backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='p',
                               blocking_enabled=lambda: False)()
    warn.assert_called_once_with(OPERATION)
    assert execute.call_count == 3
    assert 'action=continue_restart' in caplog.text
    assert 'AICODING_RESTART_FAILED' not in caplog.text


@pytest.mark.parametrize('detected', [detection('skip_no_fastdisk'), ConnectionError('probe failed')])
def test_nonfastdisk_never_reads_blocking_config(detected):
    config = Mock(side_effect=AssertionError('must not read config'))
    execute = executor([response('failed')], detected=detected)
    backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='p',
                           blocking_enabled=config)()
    assert execute.call_count == 2
    config.assert_not_called()


def test_guard_config_is_snapshotted_across_replicas_and_verification():
    config = Mock()
    config.get_value.side_effect = [False, True]
    strategy = AicodingProvisioningStrategy('aicoding', common_config_provider=lambda: config)
    runtime = runtime_with(executor([committed(), committed(), response('failed'), response('failed')]))
    runtime.list_devices_by_bot_uuid.return_value = [
        {'provider_device_id': p, 'status': 'ACTIVE'} for p in ('one', 'two')]
    verify = strategy._prepare_restart(
        BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'),
        device_id='old', restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION)
    verify()
    config.get_value.assert_called_once()


@pytest.mark.parametrize('enabled, stored, expected', [
    ('1', 'false', False), ('1', 'true', True), ('0', 'true', False),
    ('1', 'null', False), ('1', 'garbage', False),
])
def test_common_config_storage_semantics(enabled, stored, expected):
    from agentclaw.community.core.common_config.service import CommonConfigService

    repository = Mock()
    repository.get_by_biz_param.return_value = SimpleNamespace(enable=enabled, param_value=stored)
    strategy = AicodingProvisioningStrategy(
        'aicoding', common_config_provider=lambda: CommonConfigService(repository),
    )
    assert strategy._fastdisk_backup_blocking() is expected
    repository.get_by_biz_param.return_value = None
    assert strategy._fastdisk_backup_blocking() is False


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['baas', 'arca'])
@pytest.mark.parametrize('outcome, blocking', [('failed', False), ('partial_success', False), ('partial_success', True)])
async def test_durable_restart_accepts_nonblocking_failure_or_valid_partial(lifecycle, provider, outcome, blocking, monkeypatch, caplog):
    from copy import deepcopy
    from agentclaw.community.core.bot_management.engines.registry import resolve_restart_strategy
    from agentclaw.community.core.bot_management.engines.aicoding.restart_arca import ArcaRestartTransport
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import TASK_TYPE, task_key, journal
    from agentclaw.community.core.task_queue.types import Reschedule

    s = lifecycle
    config = Mock()
    config.get_value.return_value = blocking
    _, strategy = resolve_restart_strategy(s.repo.bot)
    monkeypatch.setattr(strategy, '_common_config_provider', lambda: config)
    s.binding.update(device_provider=provider, device_props={'sandbox_id': 'old-sandbox@8'})
    s.service._resolve_restart_target_provider = Mock(return_value=provider)
    s.platform.list_devices_by_bot_uuid.return_value = [{'provider_device_id': 'physical', 'status': 'ACTIVE'}]
    s.service._restart_backup_dependencies = RestartBackupDependencies(
        baas_runtime=lambda: s.platform,
        arca_transport=lambda: ArcaRestartTransport(lambda **kw: execute(kw['shell_cmd'])),
    )
    events = []
    if provider == 'arca':
        s.service.stop_bot = Mock(side_effect=lambda **_: events.append('stop'))

        def start(**kwargs):
            events.append('start')
            s.repo.bot.update(binding_id=8, status='PENDING')
            s.binding.update(id=8, device_id='new', device_props={'sandbox_id': 'new-sandbox'})
            return deepcopy(s.repo.bot)

        s.service.start_bot = Mock(side_effect=start)
    await s.service.restart_bot_async(bot_id='b', user_id='o')
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key('b', 'o'))
    op = task.payload['operation_id']
    result = partial_response(operation_id=op) if outcome == 'partial_success' else response('failed', operation_id=op)
    execute = executor([result] * 4)

    def post(**kwargs):
        if kwargs['path'].endswith('/commands'):
            return execute(kwargs['payload']['cmd'])
        events.append('update')
        return {'publish_id': 12}

    s.platform.post_bots_api.side_effect = post
    assert isinstance(s.handler.handle(task.payload), Reschedule)
    assert events == (['stop', 'start'] if provider == 'arca' else ['update'])
    assert execute.call_count == (2 if outcome == 'failed' else 3 if provider == 'arca' else 4)
    if outcome == 'partial_success':
        assert 'phase=verify status=partial_success' in caplog.text
    assert journal(s.repo.bot)['phase'] == 'WAITING_READY'
    progress = strategy.project_status_ext(ext=s.repo.bot['ext'], response_ext={})['aicoding']['restart']
    if outcome == 'failed':
        assert progress['status'] == 'RUNNING'
        assert progress['warning']['code'] == 'backup_failed_continue'
        assert '不阻断' in progress['warning']['message']
        assert '数据丢失风险' in progress['warning']['message']
        assert s.repo.bot['ext']['unrelated'] == {'keep': True}
    else:
        assert 'warning' not in progress  # A valid partial backup remains success.
    assert s.repo.bot['ext'].get('start_status') != 'FAILED'
    assert 'action=continue_restart' in caplog.text
    assert 'AICODING_RESTART_FAILED' not in caplog.text
    config.get_value.assert_called_once()
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import RestartState
    RestartState(s.repo, 'b', 'o', op).update({'WAITING_READY'}, phase='SUCCEEDED')
    completed = strategy.project_status_ext(ext=s.repo.bot['ext'], response_ext={})['aicoding']['restart']
    assert completed['status'] == 'SUCCEEDED'
    assert completed.get('warning') == progress.get('warning')


def test_disabled_guard_still_blocks_arca_binding_change():
    from agentclaw.community.core.bot_management.engines.aicoding.restart_arca import (
        ArcaRestartTransport, RestartBackupTargetChanged,
    )
    from tests.community.core.bot_management.aicoding.test_restart_dependencies import binding_args, context

    args = binding_args('arca')
    config = Mock()
    config.get_value.return_value = False
    strategy = AicodingProvisioningStrategy('aicoding', common_config_provider=lambda: config)
    execute = executor([response('failed')])
    verify = strategy.prepare_restart(context('aicoding'), **args, restart_dependencies=RestartBackupDependencies(
        arca_transport=lambda: ArcaRestartTransport(lambda **kw: execute(kw['shell_cmd'])),
    ))
    args['device_service_provider']().get_device.return_value.device_props = {'sandbox_id': 'changed'}
    with pytest.raises(RestartBackupTargetChanged):
        verify()


@pytest.mark.parametrize('configuration', ['missing', 'disabled', 'unavailable', 'no_provider', 'old_key'])
def test_unconfigured_or_unavailable_guard_does_not_block_fastdisk_failure(configuration, caplog):
    from agentclaw.community.core.common_config.service import CommonConfigService

    repository = Mock()
    if configuration == 'disabled':
        repository.get_by_biz_param.return_value = SimpleNamespace(enable='0', param_value='true')
    elif configuration == 'unavailable':
        repository.get_by_biz_param.side_effect = RuntimeError('private config error')
    elif configuration == 'old_key':
        repository.get_by_biz_param.side_effect = lambda **kw: (
            SimpleNamespace(enable='1', param_value='false') if kw['param_code'] == 'fastdisk_fail_open' else None)
    else:
        repository.get_by_biz_param.return_value = None
    config = CommonConfigService(repository)
    strategy = AicodingProvisioningStrategy(
        'aicoding', common_config_provider=None if configuration == 'no_provider' else lambda: config,
    )
    execute = executor([response('failed')])
    runtime = runtime_with(execute)
    strategy._prepare_restart(
        BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'),
        device_id='old', restart_dependencies=RestartBackupDependencies.for_instance(runtime),
        operation_id=OPERATION,
    )()
    assert execute.call_count == 2
    assert 'action=continue_restart' in caplog.text
    assert 'AICODING_RESTART_FAILED' not in caplog.text
    assert 'private config error' not in caplog.text
    if configuration == 'unavailable':
        assert 'action=keep_nonblocking' in caplog.text


def test_nonblocking_warning_write_failure_escapes_nested_baas_policy():
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import current_restart

    state = Mock()
    state.read.return_value = {'ext': {}}
    state.update.side_effect = RuntimeError('database unavailable')
    restart_scope = current_restart.set(SimpleNamespace(
        state=state, operation_id=OPERATION,
        payload={'started_at': backup.time.time(), 'device_id': 'old', 'provider': 'baas'},
    ))
    runtime = runtime_with(executor([response('failed')]))
    devices = Mock()
    devices.get_device.return_value = dict(device_id='old', device_provider='baas')
    try:
        with pytest.raises(backup.RestartBackupBlocked) as raised:
            AicodingProvisioningStrategy('aicoding')._prepare_restart(
                BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'),
                binding_id=7, device_service=devices, bot_repository=Mock(),
                restart_dependencies=RestartBackupDependencies.for_instance(runtime), operation_id=OPERATION,
                blocking_enabled=lambda: False,
            )
        assert raised.value.reason == 'warning_unavailable'
        assert isinstance(raised.value.__cause__, RuntimeError)
    finally:
        current_restart.reset(restart_scope)
