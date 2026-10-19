"""Recovery must distinguish a confirmed empty failed Bot from a stale list."""
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import RestartBackupDependencies

from agentclaw.community.core.bot_management.engines.aicoding import restart_backup as backup
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext

LIVE = [{'status': 'ACTIVE', 'provider_device_id': 'physical-1'}]


def runtime_for(status):
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.return_value = []
    runtime.get_bot.return_value = {'bot_uuid': 'target', 'status': status}
    return runtime


def prepare(runtime, engine='aicoding'):
    return AicodingProvisioningStrategy(engine)._prepare_restart(
        BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine=engine),
        device_id='target', restart_dependencies=RestartBackupDependencies.for_instance(runtime),
    )


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.parametrize('status', ['FAILED', 'RELEASED'])
def test_confirmed_empty_terminal_bot_can_restart_with_recheck(engine, status):
    runtime = runtime_for(status)
    with patch.object(backup, 'prepare_backup') as backup_call:
        verify = prepare(runtime, engine)
        verify()
    backup_call.assert_not_called()
    runtime.post_bots_api.assert_not_called()
    assert runtime.get_bot.call_count == 2
    assert runtime.list_devices_by_bot_uuid.call_count == 4
    assert all(call.kwargs == {'bot_uuid': 'target'} for call in runtime.get_bot.call_args_list)


@pytest.mark.parametrize('detail', [
    {'status': 'ACTIVE'}, {'status': 'PENDING'}, {'status': 'UNKNOWN'},
    {'status': 'FAILED'}, {'status': 'FAILED', 'bot_uuid': 'another-bot'},
    {'status': 'RELEASED', 'bot_uuid': 'another-bot'},
    {'status': 'RELEASED', 'bot_uuid': None},
    {}, None,
])
def test_unconfirmed_empty_inventory_degrades(detail, caplog):
    runtime = runtime_for('FAILED')
    runtime.get_bot.return_value = detail
    prepare(runtime)()
    assert 'status=degraded' in caplog.text
    runtime.post_bots_api.assert_not_called()


def test_existing_not_found_contract_can_recover():
    runtime = runtime_for('RELEASED')
    runtime.get_bot.return_value = {'status': 'RELEASED'}
    prepare(runtime)()


@pytest.mark.parametrize('phase', ['prepare', 'verify'])
def test_status_lookup_failure_degrades_without_leaking_error(phase, caplog):
    runtime = runtime_for('FAILED')
    if phase == 'verify':
        verify = prepare(runtime)
    runtime.get_bot.side_effect = RuntimeError('secret-response')
    verify() if phase == 'verify' else prepare(runtime)
    assert 'status=degraded' in caplog.text
    assert 'secret-response' not in caplog.text
    runtime.post_bots_api.assert_not_called()


def test_recheck_degrades_if_bot_is_no_longer_terminal(caplog):
    runtime = runtime_for('FAILED')
    verify = prepare(runtime)
    runtime.get_bot.return_value = {'bot_uuid': 'target', 'status': 'PENDING'}
    verify()
    assert 'status=degraded' in caplog.text


def test_recheck_continues_if_device_appears():
    runtime = runtime_for('FAILED')
    verify = prepare(runtime)
    runtime.list_devices_by_bot_uuid.return_value = LIVE
    verify()
    runtime.post_bots_api.assert_not_called()


@pytest.mark.parametrize('phase', ['prepare', 'verify'])
def test_device_appearing_during_confirmation_never_gets_backed_up(phase):
    runtime = runtime_for('FAILED')
    if phase == 'verify':
        verify = prepare(runtime)
    runtime.list_devices_by_bot_uuid.side_effect = [[], LIVE]
    with patch.object(backup, 'prepare_backup') as backup_call:
        verify() if phase == 'verify' else prepare(runtime)
    backup_call.assert_not_called()
    runtime.post_bots_api.assert_not_called()


def test_failed_bot_with_live_device_attempts_backup_then_degrades(caplog):
    runtime = runtime_for('FAILED')
    runtime.list_devices_by_bot_uuid.return_value = LIVE
    with patch.object(backup, 'prepare_backup', side_effect=RuntimeError('backup failed')):
        prepare(runtime)()
    assert 'status=degraded' in caplog.text
    runtime.get_bot.assert_not_called()


@pytest.mark.parametrize('inventory', [None, {}])
def test_malformed_inventory_degrades_without_claiming_recovery(inventory, caplog):
    runtime = runtime_for('FAILED')
    runtime.list_devices_by_bot_uuid.side_effect = [[], inventory]
    prepare(runtime)
    assert 'status=degraded' in caplog.text


def test_binding_path_uses_baas_state_even_after_local_bot_becomes_pending():
    runtime = runtime_for('FAILED')
    repository = Mock()
    repository.get_by_id_and_owner.return_value = {'status': 'PENDING', 'binding_id': 42}
    device_service = Mock()
    device_service.get_device.return_value = {
        'device_id': 'target', 'device_provider': 'baas', 'status': 'FAILED',
    }
    verify = AicodingProvisioningStrategy('aicoding').prepare_restart(
        BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine='aicoding'),
        binding_id=42, device_service_provider=lambda: device_service,
        restart_dependencies=RestartBackupDependencies(baas_runtime=lambda: runtime), bot_repository=repository,
    )
    verify()
    assert runtime.get_bot.call_count == 2
    repository.update_by_owner.assert_not_called()
    device_service.exec_shell_new.assert_not_called()


@pytest.mark.parametrize('phase', ['prepare', 'verify'])
def test_confirmation_inventory_query_error_degrades(phase, caplog):
    runtime = runtime_for('FAILED')
    if phase == 'verify':
        verify = prepare(runtime)
    runtime.list_devices_by_bot_uuid.side_effect = [[], RuntimeError('unavailable')]
    verify() if phase == 'verify' else prepare(runtime)
    assert 'status=degraded' in caplog.text
    runtime.post_bots_api.assert_not_called()


def test_new_malformed_device_during_confirmation_skips_backup():
    runtime = runtime_for('FAILED')
    runtime.list_devices_by_bot_uuid.side_effect = [[], [{'status': 'FAILED'}]]
    prepare(runtime)
    runtime.post_bots_api.assert_not_called()
