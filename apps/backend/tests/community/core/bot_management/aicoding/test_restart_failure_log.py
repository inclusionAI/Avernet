"""Alert-ready coding restart diagnostics, without changing lifecycle decisions."""
import logging
from unittest.mock import patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding import restart_backup as backup
from agentclaw.community.core.bot_management.engines.aicoding.restart_failure_log import (
    MARKER, arca_identity, log_restart_failure,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_state import RestartSuperseded
from tests.community.core.bot_management.aicoding.test_restart_backup import OPERATION, response
from tests.community.core.bot_management.aicoding.test_restart_fastdisk import committed, executor


@pytest.mark.parametrize(('target', 'sandbox', 'expected'), [
    ('ARCA-SANDBOX-abc@8', None, 'ARCA-SANDBOX-abc@8'),
    ('BOT-logical', 'old-sandbox@8', 'old-sandbox@8'),
    ('BOT-logical', None, '-'),
    (None, None, '-'),
    ('ARCA-SANDBOX-abc@coding', None, 'ARCA-SANDBOX-abc@coding'),
    ('ARCA-SANDBOX-abc', None, 'ARCA-SANDBOX-abc'),
])
def test_only_physical_ids_are_labeled_as_arca(target, sandbox, expected):
    assert arca_identity(target, sandbox) == expected


@pytest.mark.parametrize(('phase', 'responses'), [
    ('prepare', [response('failed')]),
    ('verify', [committed(), response('failed')]),
])
@pytest.mark.parametrize(('target', 'sandbox', 'expected'), [
    ('ARCA-SANDBOX-abc@8', None, 'ARCA-SANDBOX-abc@8'),
    ('old-sandbox@8', 'old-sandbox@8', 'old-sandbox@8'),
])
def test_strict_backup_alarm_includes_exact_container_and_failure_stage(
    phase, responses, target, sandbox, expected, caplog,
):
    execute = executor(responses)
    with pytest.raises(backup.RestartBackupBlocked):
        verify = backup._attempt_backup(
            execute=execute, operation_id=OPERATION, bot_id='b',
            target_id=target, sandbox_id=sandbox,
        )
        verify()
    alarms = [r for r in caplog.records if MARKER in r.getMessage()]
    assert alarms
    for record in alarms:
        line = record.getMessage()
        assert record.levelno == logging.ERROR
        assert f'bot_id=b arca_id={expected} target_id={target}' in line
        assert f'operation_id={OPERATION}' in line
        assert f'phase={phase} ' in line
        assert 'reason=backup_failed' in line
        assert 'helper_status=failed' in line
        assert 'restart_backup.py:' in line
        assert 'elapsed_ms=-' not in line


def test_wrapped_failure_keeps_physical_context_and_safe_cause_stack(caplog):
    try:
        try:
            raise ConnectionError('private credential\nforged_field=leak')
        except ConnectionError as original:
            log_restart_failure(
                original, bot_id='b', phase='prepare', target_id='ARCA-SANDBOX-abc@8',
                operation_id=OPERATION, helper_status='running', elapsed_ms=123,
            )
            raise backup.RestartBackupBlocked('transport_error') from original
    except backup.RestartBackupBlocked as wrapped:
        log_restart_failure(wrapped, bot_id='b', phase='task_failed', target_id='BOT-logical')
    line = caplog.records[-1].getMessage()
    assert 'arca_id=ARCA-SANDBOX-abc@8 target_id=ARCA-SANDBOX-abc@8' in line
    assert f'operation_id={OPERATION}' in line
    assert 'cause_types=RestartBackupBlocked>ConnectionError' in line
    assert 'helper_status=running' in line and 'elapsed_ms=123' in line
    assert 'test_restart_failure_log.py:' in line
    assert 'private credential' not in caplog.text and 'forged_field' not in caplog.text
    assert 'raise ConnectionError' not in caplog.text


def test_unknown_identity_and_untrusted_fields_cannot_forge_log_fields(caplog):
    log_restart_failure(bot_id='b\nstatus=success', phase='prepare', target_id='BOT-logical')
    line = caplog.records[-1].getMessage()
    assert '\n' not in line
    assert 'bot_id=b_status_success arca_id=- target_id=BOT-logical' in line
    assert 'status=success' not in line


def test_superseded_delivery_is_not_a_failed_restart_alarm(caplog):
    log_restart_failure(RestartSuperseded('no longer owns restart'), bot_id='b', phase='prepare')
    assert MARKER not in caplog.text


def test_timeout_has_container_elapsed_time_and_last_helper_status(caplog):
    execute = executor([response('running')] * 3)
    now = [0]
    with patch.object(backup, 'FASTDISK_TIMEOUT', 2), \
         patch.object(backup.time, 'monotonic', side_effect=lambda: now[0]), \
         patch.object(backup.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
        with pytest.raises(backup.RestartBackupBlocked):
            backup._attempt_backup(execute=execute, operation_id=OPERATION,
                                   bot_id='b', target_id='ARCA-SANDBOX-abc@8')
    line = next(r.getMessage() for r in caplog.records if MARKER in r.getMessage())
    assert 'reason=timeout' in line
    assert 'arca_id=ARCA-SANDBOX-abc@8 ' in line
    assert 'elapsed_ms=2000' in line
    assert 'helper_status=running' in line


@pytest.mark.parametrize('replicas', [1, 2])
@pytest.mark.parametrize('inventory_failure', [True, False])
def test_inventory_verification_failure_retains_resolved_arca_owner_and_env(caplog, monkeypatch, replicas, inventory_failure):
    from unittest.mock import Mock

    from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import RestartBackupDependencies
    from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
    from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext

    monkeypatch.setenv('SERVER_ENV', 'pre')
    runtime = Mock()
    physical = [f'ARCA-SANDBOX-{i}@8' for i in range(replicas)]
    runtime.list_devices_by_bot_uuid.side_effect = [
        [{'provider_device_id': target, 'status': 'ACTIVE'} for target in physical],
        ConnectionError('private inventory failure') if inventory_failure else
        [{'provider_device_id': 'ARCA-SANDBOX-replacement@8', 'status': 'ACTIVE'}],
    ]
    execute = executor([committed()] * replicas)
    runtime.post_bots_api.side_effect = lambda **kw: execute(kw['payload']['cmd'])
    ctx = BotProvisioningContext(bot_id='b', owner_id='owner', bot_type='personal', active_engine='aicoding')
    verify = AicodingProvisioningStrategy('aicoding')._prepare_restart(
        ctx, device_id='BOT-logical', operation_id=OPERATION,
        restart_dependencies=RestartBackupDependencies.for_instance(runtime),
    )
    with pytest.raises(backup.RestartBackupBlocked) as raised:
        verify()
    # The outer task/published summary must retain all original containers.
    log_restart_failure(raised.value, bot_id=ctx.bot_id, target_id="BOT-logical", phase="summary")
    alarms = [r.getMessage() for r in caplog.records if MARKER in r.getMessage()]
    assert alarms
    assert len(alarms) == replicas * 2
    for target in physical:
        assert sum(f'arca_id={target} ' in line for line in alarms) == 2
    assert all('ARCA-SANDBOX-replacement' not in line for line in alarms)
    assert all('owner_id=owner' in line and 'env=pre' in line for line in alarms)
    assert all('private inventory' not in line for line in alarms)
    assert runtime.list_devices_by_bot_uuid.call_count == 2  # Logging adds no lookup.


@pytest.mark.parametrize(('server_env', 'expected'), [
    ('stable', 'dev'), ('prepub', 'pre'), ('gray', 'prod'), ('prod', 'prod'),
])
def test_env_uses_existing_data_environment_and_owner_is_sanitized(server_env, expected, monkeypatch, caplog):
    monkeypatch.setenv('SERVER_ENV', server_env)
    log_restart_failure(bot_id='b', owner_id='owner\nphase=forged', phase='prepare')
    line = caplog.records[-1].getMessage()
    assert f'env={expected} ' in line
    assert 'owner_id=owner_phase_forged' in line
    assert 'phase=forged' not in line and '\n' not in line
