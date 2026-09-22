"""One engine precondition shared by published/caller lifecycle adapters."""
import asyncio
import json
import threading
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.services.instance_restart import InstanceRestartMixin

TARGET = 'published-or-caller-container'
OP = uuid.uuid5(uuid.NAMESPACE_URL, 'restart:' + TARGET + ':physical-1').hex
BOT = {'bot_id': 'source-bot', 'owner_id': 'owner', 'active_engine': 'aicoding'}


def result(status, **kw):
    return SimpleNamespace(exit_code=0, stdout=json.dumps(dict(
        version=1, operation_id=OP, boot_id='boot-1', status=status, **kw)))


def service(status='legacy'):
    svc = InstanceRestartMixin()
    svc._try_acquire_restart_lock = Mock(return_value=SimpleNamespace(lock_token='lease'))
    svc._restart_lock_repo = Mock()
    svc._restart_lock_repo.renew.return_value = True
    device = Mock()
    device.exec_shell_new.return_value = result(status)
    svc._device_service_provider = Mock(return_value=device)
    baas = Mock()
    baas.get_bot.return_value = {'status': 'ACTIVE', 'devices': [{'provider_device_id': 'physical-1', 'status': 'ACTIVE'}]}
    baas.exec_command_on_device.return_value = result(status)
    svc._baas_service_provider = Mock(return_value=baas)
    return svc, device, baas


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.parametrize('status', ['legacy', 'not_mounted', 'committed'])
async def test_target_not_source_and_no_source_state_writes(engine, status):
    svc, device, baas = service(status)
    if status == 'committed':
        baas.exec_command_on_device.return_value = result(status, backup={
            'status': 'success', 'operation_id': OP, 'generation_id': 'gen-1'})
    async with svc.instance_restart_guard(bot={**BOT, 'active_engine': engine}, device_id=TARGET):
        svc._restart_lock_repo.release.assert_not_called()
    device.get_device.assert_not_called()
    assert baas.exec_command_on_device.call_args.kwargs['paas_device_id'] == 'physical-1'
    assert all(call.kwargs['bot_uuid'] == TARGET for call in baas.get_bot.call_args_list)
    svc._restart_lock_repo.release.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [result('failed'), SimpleNamespace(exit_code=1, stdout=''),
                                     SimpleNamespace(exit_code=0, stdout='')])
async def test_backup_failure_never_enters_replacement(failure):
    svc, device, baas = service()
    baas.exec_command_on_device.return_value = failure
    replacement = Mock()
    with pytest.raises(RuntimeError):
        async with svc.instance_restart_guard(bot=BOT, device_id=TARGET):
            replacement()
    replacement.assert_not_called()
    svc._restart_lock_repo.release.assert_called_once()


@pytest.mark.asyncio
async def test_confirmed_released_target_does_not_require_old_script():
    svc, device, baas = service()
    baas.get_bot.return_value = {'status': 'RELEASED'}
    async with svc.instance_restart_guard(bot=BOT, device_id=TARGET):
        pass
    device.exec_shell_new.assert_not_called()


@pytest.mark.asyncio
async def test_query_failure_is_not_released():
    svc, device, baas = service()
    baas.get_bot.side_effect = TimeoutError('unconfirmed')
    with pytest.raises(TimeoutError):
        async with svc.instance_restart_guard(bot=BOT, device_id=TARGET):
            pytest.fail('must not replace')
    device.exec_shell_new.assert_not_called()


@pytest.mark.asyncio
async def test_unresolvable_physical_container_cannot_be_certified():
    svc, device, baas = service()
    baas.get_bot.return_value = {'status': 'ACTIVE', 'devices': [{}, {}]}
    with pytest.raises(RuntimeError, match='物理容器'):
        async with svc.instance_restart_guard(bot=BOT, device_id=TARGET):
            pytest.fail('must not replace')
    device.exec_shell_new.assert_not_called()


@pytest.mark.asyncio
async def test_other_engine_does_not_probe_runtime():
    svc, device, baas = service()
    async with svc.instance_restart_guard(bot={**BOT, 'active_engine': 'openclaw'}, device_id=TARGET):
        pass
    device.exec_shell_new.assert_not_called()
    baas.get_bot.assert_not_called()


@pytest.mark.asyncio
async def test_target_busy_does_not_start_backup():
    svc, device, _ = service()
    svc._try_acquire_restart_lock.return_value = None
    with pytest.raises(RuntimeError, match='正在重启'):
        async with svc.instance_restart_guard(bot=BOT, device_id=TARGET):
            pytest.fail('must not replace')
    device.exec_shell_new.assert_not_called()
    svc._restart_lock_repo.release.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_does_not_release_lock_while_exec_runs():
    svc, device, baas = service()
    started, finish = threading.Event(), threading.Event()

    def execute(**kw):
        started.set()
        assert finish.wait(5)
        return result('legacy')

    baas.exec_command_on_device.side_effect = execute

    async def restart():
        async with svc.instance_restart_guard(bot=BOT, device_id=TARGET):
            pytest.fail('cancelled request must not replace')

    task = asyncio.create_task(restart())
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    svc._restart_lock_repo.release.assert_not_called()
    finish.set()
    for _ in range(100):
        if svc._restart_lock_repo.release.called:
            break
        await asyncio.sleep(.01)
    svc._restart_lock_repo.release.assert_called_once()


@pytest.mark.asyncio
async def test_legacy_replicas_each_probe_their_own_physical_container():
    svc, _, baas = service()
    baas.get_bot.return_value = {'status': 'ACTIVE', 'devices': [
        {'provider_device_id': 'physical-1', 'status': 'ACTIVE'},
        {'provider_device_id': 'physical-2', 'status': 'ACTIVE'},
        {'provider_device_id': 'historical', 'status': 'RELEASED'},
    ]}

    def execute(*, paas_device_id, cmd):
        operation = uuid.uuid5(uuid.NAMESPACE_URL, 'restart:' + TARGET + ':' + paas_device_id).hex
        return {'exit_code': 0, 'stdout': json.dumps({
            'version': 1, 'operation_id': operation, 'status': 'legacy'})}

    baas.exec_command_on_device.side_effect = execute
    async with svc.instance_restart_guard(bot=BOT, device_id=TARGET):
        pass
    assert [c.kwargs['paas_device_id'] for c in baas.exec_command_on_device.call_args_list] == [
        'physical-1', 'physical-2']


@pytest.mark.asyncio
async def test_topology_changed_after_backup_blocks_replacement():
    svc, _, baas = service()
    before = baas.get_bot.return_value
    baas.get_bot.side_effect = [before, {'status': 'ACTIVE', 'devices': [
        {'provider_device_id': 'new-physical', 'status': 'ACTIVE'}]}]
    with pytest.raises(RuntimeError, match='清单变化'):
        async with svc.instance_restart_guard(bot=BOT, device_id=TARGET):
            pytest.fail('must not replace')


@pytest.mark.asyncio
async def test_released_physical_devices_need_no_script():
    svc, _, baas = service()
    baas.get_bot.return_value = {'status': 'STOPPED', 'devices': [
        {'provider_device_id': 'historical', 'status': 'STOPPED'}]}
    async with svc.instance_restart_guard(bot=BOT, device_id=TARGET):
        pass
    baas.exec_command_on_device.assert_not_called()
