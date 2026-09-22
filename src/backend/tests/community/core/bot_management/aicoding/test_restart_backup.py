"""Coding-only restart preconditions, receipt fencing, and sanitized logs."""
import json
import logging
import re
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
    caplog.set_level(logging.INFO, logger=backup.logger.name)
    execute = Mock(return_value=response(status))
    verify = prepare(execute)
    assert execute.call_count == 1
    verify()
    assert execute.call_count == 2
    assert 'phase=prepared status=' + status in caplog.text
    assert 'phase=verify status=allowed' in caplog.text
    assert 'bot_id=bot-1 target_id=container-1 operation_id=' + OPERATION in caplog.text


def test_poll_and_short_verification_require_matching_receipt(caplog):
    caplog.set_level(logging.INFO, logger=backup.logger.name)
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
    caplog.set_level(logging.INFO, logger=backup.logger.name)
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
    caplog.set_level(logging.INFO, logger=backup.logger.name)
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
        verify = AicodingProvisioningStrategy(engine)._prepare_restart(
            ctx, binding_id=42, device_service=device, bot_repository=repository)
    with pytest.raises(RuntimeError, match='绑定'):
        verify()
    receipt_check.assert_not_called()
    repository.update_by_owner.assert_not_called()


def test_default_engine_is_noop():
    device = Mock()
    assert DefaultProvisioningStrategy().prepare_restart(None, binding_id=42, device_service=device) is None
    device.get_device.assert_not_called()


@pytest.mark.asyncio
async def test_other_engine_instance_does_not_probe_or_lock():
    runtime = Mock()
    await prepare_instance_restart(bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': 'openclaw'},
                                   device_id='target', target_runtime=runtime)
    assert not runtime.mock_calls


def test_active_empty_inventory_fails_closed():
    runtime = Mock()
    runtime.get_bot.return_value = {'status': 'ACTIVE', 'devices': []}
    with pytest.raises(RuntimeError, match='清单为空'):
        AicodingProvisioningStrategy('aicoding')._prepare_restart(
            BotProvisioningContext(
                bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'
            ),
            device_id='caller-uuid', target_runtime=runtime,
        )


def test_operation_id_is_fresh_per_restart_request():
    ctx = BotProvisioningContext(
        bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'
    )
    runtime = Mock()
    runtime.get_bot.return_value = {
        'status': 'ACTIVE',
        'devices': [{'provider_device_id': 'physical-1', 'status': 'ACTIVE'}],
    }
    with patch.object(backup, 'prepare_backup', side_effect=lambda **kwargs: Mock()) as prepared:
        strategy = AicodingProvisioningStrategy('aicoding')
        strategy._prepare_restart(ctx, device_id='caller-uuid', target_runtime=runtime)
        strategy._prepare_restart(ctx, device_id='caller-uuid', target_runtime=runtime)
    ids = [call.kwargs['operation_id'] for call in prepared.call_args_list]
    assert len(ids) == 2 and ids[0] != ids[1]


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
    runtime.post_bots_api.assert_called_once_with(
        path='/api/v1/paas/devices/physical-1/commands',
        payload={'cmd': 'probe'}, action='aicoding_restart_backup')


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
        FakeRestartLockRepo, _make_service, _make_bot, _stateful_bot_repository,
    )
    locks = FakeRestartLockRepo()
    bot = _make_bot(active_engine='aicoding', binding_id=42)
    repository, state = _stateful_bot_repository(bot)
    device = Mock()
    device.get_device.return_value = {'device_id': 'old-container', 'device_provider': provider, 'status': 'ACTIVE'}
    runtime = Mock()
    runtime.get_bot.return_value = {'devices': [{'provider_device_id': 'physical', 'status': 'ACTIVE'}]}
    svc = _make_service(locks, bot_repository=repository, device_provider=device,
                        baas_service_provider=lambda: runtime)

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
        # prepare fails BEFORE the caller's lock exists (raw error, no lock to
        # clean up); verify fails INSIDE the caller's try (wrapped by the
        # existing restart error path, lock released by its finally).
        from agentclaw.community.core.bot_management.services.bot_service import BotServiceError
        expected = RuntimeError if phase == 'prepare' else BotServiceError
        with pytest.raises(expected, match='backup failed'):
            svc.restart_bot(bot_id='bot001', user_id='user001')
    stop.assert_not_called()
    start.assert_not_called()
    update.assert_not_called()
    assert state['binding_id'] == 42 and state['status'] == 'ACTIVE'
    assert not locks.rows


def test_wait_logs_are_throttled_but_keep_progress(caplog):
    caplog.set_level(logging.INFO, logger=backup.logger.name)
    now = [0]
    committed = response('committed', backup={
        'status': 'success', 'generation_id': 'g1', 'operation_id': OPERATION})
    execute = Mock(side_effect=[response('running')] * 32 + [committed])
    with patch.object(backup.time, 'monotonic', side_effect=lambda: now[0]), \
         patch.object(backup.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
        backup.prepare_backup(execute=execute, operation_id=OPERATION, bot_id='b', target_id='t')
    assert caplog.text.count('phase=wait status=running') == 2
    assert 'phase=prepared status=committed' in caplog.text


@pytest.mark.parametrize('engine', ['openclaw', 'teclaw', 'qoder', 'unknown'])
@pytest.mark.asyncio
async def test_http_dispatch_preserves_non_coding_execution_thread(engine):
    import threading
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    service = Mock()
    service.get_bot.return_value = {'active_engine': engine}
    current_thread = threading.get_ident()
    service.restart_bot.side_effect = lambda **kwargs: threading.get_ident()
    with patch.object(backup.asyncio, 'to_thread', side_effect=AssertionError('unexpected offload')):
        assert await BotService.restart_bot_async(service, bot_id='b', user_id='o') == current_thread
    service.restart_bot.assert_called_once_with(bot_id='b', user_id='o')


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.asyncio
async def test_coding_execution_keeps_event_loop_available(engine):
    import asyncio
    import threading
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    service = Mock()
    service.get_bot.return_value = {'active_engine': engine}
    started, release = threading.Event(), threading.Event()
    current_thread = threading.get_ident()

    def operation(**kwargs):
        started.set()
        assert release.wait(5)
        return threading.get_ident()

    service.restart_bot.side_effect = operation
    task = asyncio.create_task(BotService.restart_bot_async(service, bot_id='b', user_id='o'))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(.01)
        assert started.is_set() and not task.done()
    finally:
        release.set()
    assert await task != current_thread


def test_default_hook_is_lock_free_noop():
    provider = Mock()
    assert DefaultProvisioningStrategy().prepare_restart(
        None, device_service_provider=provider, binding_id=42) is None
    provider.assert_not_called()


def test_coding_prepare_returns_verifier_without_touching_any_lock():
    ctx = BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding')
    strategy = AicodingProvisioningStrategy('aicoding')
    verify = Mock()
    with patch.object(strategy, '_prepare_restart', return_value=verify) as prepare:
        assert strategy.prepare_restart(ctx) is verify
        prepare.assert_called_once()
        kwargs = prepare.call_args.kwargs
        assert kwargs['target_runtime_provider'] is None
        assert re.fullmatch(r'[a-f0-9]{32}', kwargs['operation_id'])
        verify.assert_not_called()  # The caller decides when to verify (under its lock).


def test_no_binding_does_not_resolve_device_service():
    ctx = BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding')
    provider = Mock(side_effect=AssertionError('device provider should not be resolved'))
    verify = AicodingProvisioningStrategy('aicoding').prepare_restart(
        ctx, binding_id=None, device_service_provider=provider)
    verify()  # No-op verifier: nothing probed, nothing to recheck.
    provider.assert_not_called()


def _run_absent_helper_probe(cmd, *, present_paths=()):
    """Execute the actual generated probe with the old container filesystem view."""
    import contextlib
    import io
    import os
    import shlex

    def lstat(path):
        if path in present_paths:
            return SimpleNamespace()
        raise FileNotFoundError(path)

    output = io.StringIO()
    code = shlex.split(cmd)[-1]
    with patch.object(os, 'lstat', side_effect=lstat), contextlib.redirect_stdout(output):
        exec(compile(code, '<old-container-probe>', 'exec'), {})
    return SimpleNamespace(exit_code=0, stdout=output.getvalue())


def test_old_fastdisk_marker_does_not_claim_new_backup_capability():
    result = _run_absent_helper_probe(backup.command('start', OPERATION),
                                     present_paths={'/opt/.aicoding/.fastdisk.ready'})
    assert backup.parse_result(result, OPERATION)['status'] == 'legacy'


@pytest.mark.parametrize('marker', ['/opt/agentclaw/restart-backup-v1',
                                   '/run/agentclaw-restart-backup/barrier'])
def test_missing_helper_after_new_capability_install_is_not_legacy(marker):
    with pytest.raises(RuntimeError, match='capability missing'):
        _run_absent_helper_probe(backup.command('start', OPERATION), present_paths={marker})


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.parametrize('provider', ['arca', 'baas'])
@pytest.mark.parametrize('binding_status', ['ACTIVE', 'PENDING', 'FAILED', 'STOPPED'])
def test_old_coding_bot_without_new_script_completes_original_restart(engine, provider, binding_status, caplog):
    caplog.set_level(logging.INFO, logger=backup.logger.name)
    from tests.community.core.bot_management.services.test_bot_service_restart_idempotency import (
        FakeRestartLockRepo, _make_service, _make_bot, _stateful_bot_repository,
    )
    from tests.community.core.devices.services.test_device_service import (
        _make_service as make_device_service, _make_record,
    )
    locks = FakeRestartLockRepo()
    bot = _make_bot(active_engine=engine, binding_id=42,
                    status='PENDING' if binding_status == 'PENDING' else 'ACTIVE')
    repository, _ = _stateful_bot_repository(bot)
    record = _make_record(id=42, device_id='legacy-container', device_provider=provider,
                          status=binding_status, device_props={"sandbox_id": "ARCA-SANDBOX-legacy@0"})
    device_repo = Mock()
    device_repo.get_by_id.return_value = device_repo.get_by_device_id.return_value = record
    device = make_device_service(repo=device_repo)
    device._exec_shell_new = Mock(side_effect=lambda **kw: _run_absent_helper_probe(kw['shell_cmd']))
    runtime = Mock()
    runtime.get_bot.return_value = {'devices': [{'provider_device_id': 'physical-legacy', 'status': 'ACTIVE'}]}
    runtime.post_bots_api.side_effect = lambda **kw: vars(_run_absent_helper_probe(kw['payload']['cmd']))
    svc = _make_service(locks, bot_repository=repository, device_provider=device,
                        baas_service_provider=lambda: runtime)
    with patch.object(svc, 'stop_bot', return_value=True) as stop, \
         patch.object(svc, 'start_bot', return_value=bot) as start, \
         patch.object(svc, '_restart_bot_baas', return_value=bot) as update:
        assert svc.restart_bot(bot_id='bot001', user_id='user001') == bot
    if provider == 'baas' or binding_status in {'FAILED', 'STOPPED'}:
        device._exec_shell_new.assert_not_called()
        assert runtime.post_bots_api.call_count == 2
    else:
        assert device._exec_shell_new.call_count == 2
        runtime.post_bots_api.assert_not_called()
    assert 'status=legacy' in caplog.text and 'reason=helper_absent' in caplog.text
    if provider == 'arca':
        stop.assert_called_once()
        start.assert_called_once()
        update.assert_not_called()
        assert locks.rows  # Original allocation owns the lock hand-off.
    else:
        update.assert_called_once()
        stop.assert_not_called()
        start.assert_not_called()
        assert not locks.rows


@pytest.mark.parametrize('engine', ['openclaw', 'moltis', 'hermes', 'unknown'])
@pytest.mark.parametrize('provider', ['arca', 'baas'])
def test_non_coding_original_restart_never_probes_even_with_coding_template(engine, provider):
    from tests.community.core.bot_management.services.test_bot_service_restart_idempotency import (
        FakeRestartLockRepo, _make_service, _make_bot, _stateful_bot_repository,
    )
    bot = _make_bot(active_engine=engine, binding_id=42, template_type='personalCoding')
    repository, _ = _stateful_bot_repository(bot)
    device = Mock()
    device.get_device.return_value = {'device_id': 'legacy-container',
                                     'device_provider': provider, 'status': 'ACTIVE'}
    device.exec_shell_new.side_effect = AssertionError('non-coding must never probe')
    svc = _make_service(FakeRestartLockRepo(), bot_repository=repository, device_provider=device,
                        baas_service_provider=lambda: Mock())
    with patch.object(svc, 'stop_bot', return_value=True) as stop, \
         patch.object(svc, 'start_bot', return_value=bot) as start, \
         patch.object(svc, '_restart_bot_baas', return_value=bot) as update, \
         patch.object(backup, 'prepare_backup', side_effect=AssertionError('unexpected coding backup')):
        assert svc.restart_bot(bot_id='bot001', user_id='user001') == bot
    device.exec_shell_new.assert_not_called()
    assert update.call_count == (1 if provider == 'baas' else 0)
    assert stop.call_count == start.call_count == (1 if provider == 'arca' else 0)


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.asyncio
async def test_legacy_published_or_caller_instance_without_script_is_allowed(engine):
    runtime = Mock()
    runtime.get_bot.return_value = {'devices': [
        {'provider_device_id': 'physical-legacy', 'status': 'ACTIVE'}]}
    runtime.post_bots_api.side_effect = lambda **kw: vars(_run_absent_helper_probe(kw['payload']['cmd']))
    await prepare_instance_restart(bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': engine},
                                   device_id='instance-legacy', target_runtime=runtime)
    assert runtime.post_bots_api.call_count == 2
    assert all(call.kwargs['path'] == '/api/v1/paas/devices/physical-legacy/commands'
               for call in runtime.post_bots_api.call_args_list)


@pytest.mark.parametrize('engine', ['openclaw', 'teclaw', 'hermes', 'moltis', 'unknown', None])
@pytest.mark.asyncio
async def test_non_coding_instance_skips_all_backup_dependencies(engine):
    runtime = Mock()
    with patch.object(backup, 'prepare_backup', side_effect=AssertionError('unexpected backup')), \
         patch.object(backup.asyncio, 'to_thread', side_effect=AssertionError('unexpected offload')):
        await prepare_instance_restart(bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': engine},
                                       device_id='target', target_runtime=runtime)
    assert not runtime.mock_calls


@pytest.mark.parametrize('status', ['ACTIVE', 'PENDING', 'FAILED', 'STOPPED', 'RELEASED', 'UNKNOWN'])
def test_shared_command_status_gate_remains_unchanged(status):
    from tests.community.core.devices.services.test_device_service import _make_service, _make_record
    from agentclaw.community.core.devices.errors import InvalidDeviceStatusError
    repo = Mock()
    repo.get_by_device_id.return_value = _make_record(status=status)
    svc = _make_service(repo=repo)
    svc._exec_shell_new = Mock(return_value='original-result')
    allowed = status in {'ACTIVE', 'PENDING'}
    if allowed:
        assert svc.exec_shell_new('device', 'probe') == 'original-result'
        svc._exec_shell_new.assert_called_once()
    else:
        with pytest.raises(InvalidDeviceStatusError):
            svc.exec_shell_new('device', 'probe')
        svc._exec_shell_new.assert_not_called()
    repo.update_status.assert_not_called()


def test_physical_command_reuses_unchanged_public_baas_api():
    from tests.community.core.service_bot.services.test_baas_service_exec_command import _make_service
    runtime, _ = _make_service()
    result = {'exit_code': 0, 'stdout': 'result'}
    runtime._http.post.return_value.json.return_value = {'code': 0, 'data': result}
    assert backup._execute_physical(runtime, 'physical@12', 'probe') == result
    runtime._http.post.assert_called_once_with(
        '/api/v1/paas/devices/physical@12/commands', params={'tenant': runtime._tenant},
        json={'cmd': 'probe'}, timeout=30.0)


def test_physical_command_cannot_inject_another_url_path():
    runtime = Mock()
    backup._execute_physical(runtime, 'device/other?x=1', 'probe')
    assert runtime.post_bots_api.call_args.kwargs['path'] == (
        '/api/v1/paas/devices/device%2Fother%3Fx%3D1/commands')


def test_physical_command_transport_failure_is_not_legacy():
    runtime = Mock()
    runtime.post_bots_api.side_effect = TimeoutError('unavailable')
    with pytest.raises(TimeoutError):
        backup.prepare_backup(execute=lambda cmd: backup._execute_physical(runtime, 'physical', cmd),
                              operation_id=OPERATION, bot_id='b', target_id='physical')


def test_restart_policy_does_not_extend_shared_execution_apis():
    import inspect
    from agentclaw.community.core.bot_management.engines.provisioning import EngineProvisioningStrategy
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.devices.services.device_service import DeviceService
    from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter
    from agentclaw.community.core.service_bot.services.baas_service import BaasService
    assert "execute_restart" in EngineProvisioningStrategy.__dict__
    assert hasattr(BotService, "restart_bot_async")
    assert 'allow_recovery' not in inspect.signature(DeviceService.exec_shell_new).parameters
    assert 'allow_recovery' not in inspect.signature(DeviceServiceRouter.exec_shell_new).parameters
    assert 'paas_device_id' not in inspect.signature(BaasService.exec_command_on_bot).parameters


@pytest.mark.asyncio
async def test_instance_dispatch_uses_strategy_contract_not_coding_type():
    from agentclaw.community.core.bot_management.engines import registry

    class OtherStrategy(DefaultProvisioningStrategy):
        def prepare_restart(self, ctx, **kwargs):
            prepared(ctx, **kwargs)

    prepared = Mock()
    ctx = object()
    runtime = Mock()
    with patch.object(registry, 'resolve_restart_strategy', return_value=(ctx, OtherStrategy())):
        await registry.prepare_instance_restart(bot={}, device_id='target', target_runtime=runtime)
    prepared.assert_called_once_with(ctx, device_id='target', target_runtime=runtime,
                                     operation_id=None, restart_key=None)
    assert not runtime.mock_calls


@pytest.mark.asyncio
async def test_async_service_preserves_original_restart_error():
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    service = Mock()
    service.get_bot.return_value = {'active_engine': 'openclaw'}
    error = RuntimeError('original restart failure')
    service.restart_bot.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await BotService.restart_bot_async(service, bot_id='b', user_id='o')
    assert caught.value is error


def test_async_service_contract_matches_implementation():
    import inspect
    from agentclaw.community.core.bot_management.bot_service_protocol import BotServiceProtocol
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    expected = BotServiceProtocol.restart_bot_async
    actual = BotService.restart_bot_async
    assert inspect.iscoroutinefunction(expected) and inspect.iscoroutinefunction(actual)
    assert list(inspect.signature(expected).parameters) == list(inspect.signature(actual).parameters)


@pytest.mark.asyncio
async def test_http_restart_preserves_lifecycle_callback_signature():
    calls = []

    def restart(*, bot_id, user_id):
        calls.append((bot_id, user_id))
        return {'status': 'PENDING'}

    result = await AicodingProvisioningStrategy('aicoding').execute_restart(
        None, restart, bot_id='bot', user_id='owner',
    )
    assert result == {'status': 'PENDING'}
    assert calls == [('bot', 'owner')]


@pytest.mark.asyncio
async def test_durable_restart_key_is_consumed_by_coding_strategy():
    runtime = Mock()
    runtime.get_bot.return_value = {
        'status': 'ACTIVE',
        'devices': [{'provider_device_id': 'physical-1', 'status': 'ACTIVE'}],
    }
    bot = {'bot_id': 'b', 'owner_id': 'o', 'active_engine': 'aicoding'}
    with patch.object(backup, 'prepare_backup', side_effect=lambda **kwargs: Mock()) as prepared:
        for key in ['restart:publish-1:prod', 'restart:publish-1:prod', 'restart:publish-2:prod']:
            await prepare_instance_restart(
                bot=bot, device_id='target', target_runtime=runtime, restart_key=key,
            )
    ids = [call.kwargs['operation_id'] for call in prepared.call_args_list]
    assert ids[0] == ids[1]
    assert ids[0] != ids[2]


@pytest.mark.asyncio
async def test_other_engine_ignores_durable_restart_key():
    runtime = Mock()
    await prepare_instance_restart(
        bot={'bot_id': 'b', 'owner_id': 'o', 'active_engine': 'openclaw'},
        device_id='target', target_runtime=runtime, restart_key='restart:publish:prod',
    )
    assert not runtime.mock_calls
