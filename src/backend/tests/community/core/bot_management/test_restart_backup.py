"""Coding strategy/platform-exec contract; no production endpoints."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding import restart_backup as backup
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
from agentclaw.community.core.bot_management.engines.default import DefaultProvisioningStrategy
from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext

OPERATION = 'a' * 32


def response(status, **values):
    return SimpleNamespace(exit_code=0, stdout=json.dumps(dict(
        version=1, operation_id=OPERATION, boot_id='instance-1', status=status, **values)))


@pytest.fixture
def device():
    device = Mock()
    device.get_device.return_value = {'device_id': 'sandbox-1'}
    return device


def prepare(device, renew=None, on_preparing=None):
    with patch.object(backup.uuid, 'uuid4', return_value=SimpleNamespace(hex=OPERATION)), patch.object(backup.time, 'sleep'):
        return backup.prepare_backup(execute=lambda cmd: device.exec_shell_new(device_id="sandbox-1", shell_cmd=cmd),
                                     renew_lease=renew or Mock(return_value=True),
                                     on_preparing=on_preparing or Mock())


@pytest.mark.parametrize('status', ['legacy', 'not_mounted'])
def test_confirmed_no_backup_returns_without_stopping(device, status):
    device.exec_shell_new.return_value = response(status)
    preparing = Mock()
    prepare(device, on_preparing=preparing)
    preparing.assert_not_called()
    device.exec_shell_new.assert_called_once()


def test_poll_requires_matching_backup_receipt(device):
    device.exec_shell_new.side_effect = [response('running'), response('committed', backup={
        'status': 'success', 'generation_id': 'g1', 'operation_id': OPERATION})]
    preparing = Mock()
    renew = Mock(return_value=True)
    prepare(device, renew, preparing)
    preparing.assert_called_once()
    assert renew.call_count == 3
    assert 'status' in device.exec_shell_new.call_args.kwargs['shell_cmd']


@pytest.mark.parametrize('result', [SimpleNamespace(exit_code=1, stdout=''),
    SimpleNamespace(exit_code=0, stdout=''), response('failed'),
    response('committed'), response('skipped')])
def test_ambiguous_or_failed_backup_raises(device, result):
    device.exec_shell_new.return_value = result
    with pytest.raises(RuntimeError):
        prepare(device)


def test_script_disappearing_during_poll_is_not_legacy(device):
    device.exec_shell_new.side_effect = [response('running'), response('legacy')]
    with pytest.raises(RuntimeError, match='消失'):
        prepare(device)


def test_instance_changed_during_poll_blocks(device):
    newer = response('running')
    value = json.loads(newer.stdout)
    value['boot_id'] = 'instance-2'
    newer.stdout = json.dumps(value)
    device.exec_shell_new.side_effect = [response('running'), newer]
    with pytest.raises(RuntimeError, match='身份'):
        prepare(device)


def test_lost_lease_does_not_execute(device):
    with pytest.raises(RuntimeError, match='锁'):
        prepare(device, Mock(return_value=False))
    device.exec_shell_new.assert_not_called()


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
def test_failure_preserves_binding_and_marks_failed(engine, device):
    device.exec_shell_new.return_value = response('failed')
    repository = Mock()
    ctx = BotProvisioningContext(bot_id='bot', owner_id='owner', bot_type='personal', active_engine=engine)
    with patch.object(backup.uuid, 'uuid4', return_value=SimpleNamespace(hex=OPERATION)), pytest.raises(RuntimeError):
        AicodingProvisioningStrategy(engine).prepare_restart(ctx, binding_id=42,
            device_service=device, bot_repository=repository, renew_lease=lambda: True)
    assert repository.update_by_owner.call_args.args == ('bot', 'owner', {'status': 'FAILED'})
    for call in repository.update_by_owner.call_args_list:
        assert 'binding_id' not in call.args[2]


def test_default_engine_is_noop(device):
    DefaultProvisioningStrategy().prepare_restart(None, binding_id=42,
        device_service=device, bot_repository=Mock(), renew_lease=Mock())
    device.get_device.assert_not_called()


def test_probe_catches_only_missing_not_permission_errors():
    command = backup.command('start', OPERATION)
    assert 'except FileNotFoundError' in command
    assert 'except OSError' not in command
    assert 'os.X_OK' in command
    assert 'sudo' in command


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("I/O")])
def test_probe_access_errors_are_not_legacy(error):
    import os
    import shlex
    code = shlex.split(backup.command('start', OPERATION))[-1]
    with patch.object(os, 'lstat', side_effect=error), pytest.raises(type(error)):
        exec(compile(code, '<restart-probe>', 'exec'), {})


def test_probe_only_confirmed_absence_is_legacy(capsys):
    import os
    import shlex
    code = shlex.split(backup.command('start', OPERATION))[-1]
    with patch.object(os, 'lstat', side_effect=FileNotFoundError()):
        exec(compile(code, '<restart-probe>', 'exec'), {})
    assert json.loads(capsys.readouterr().out)['status'] == 'legacy'


def test_missing_script_with_upgraded_marker_blocks():
    import os
    import shlex
    code = shlex.split(backup.command('start', OPERATION))[-1]
    with patch.object(os, 'lstat', side_effect=[FileNotFoundError(), SimpleNamespace()]), pytest.raises(RuntimeError, match='upgraded'):
        exec(compile(code, '<restart-probe>', 'exec'), {})
