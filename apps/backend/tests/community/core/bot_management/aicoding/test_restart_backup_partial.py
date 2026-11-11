"""Published partial copies count as restart success, with distinct warning logs."""
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding import restart_backup as backup
from tests.community.core.bot_management.aicoding.test_restart_backup import OPERATION, partial_response, response
from tests.community.core.bot_management.aicoding.test_restart_fastdisk import committed, detection, executor


@pytest.mark.parametrize('blocking', [False, True])
@pytest.mark.parametrize('detected', [detection(), detection('skip_no_fastdisk'), ConnectionError('private probe')])
@pytest.mark.parametrize('result', [partial_response(), partial_response(completeness='degraded'),
                                    committed(completeness='degraded')])
def test_partial_receipts_are_only_read_for_confirmed_fastdisk(blocking, detected, result, caplog, monkeypatch):
    monkeypatch.setenv('SERVER_ENV', 'pre')
    execute = executor([response('running'), result, result], detected=detected)
    with patch.object(backup.time, 'sleep'):
        check = backup._attempt_backup(
            execute=execute, operation_id=OPERATION, bot_id='b', target_id='ARCA-SANDBOX-p@8',
            owner_id='o', blocking_enabled=lambda: blocking,
        )
        check()
    if isinstance(detected, Exception) or json.loads(detected.stdout)['status'] != 'run':
        assert execute.call_count == 1  # No helper or partial receipt without live mounts.
        assert 'event=aicoding_restart_backup' not in caplog.text
        assert 'status=partial_success' not in caplog.text
        assert check.required is False
        return
    assert execute.call_count == 3
    assert execute.call_args.args == (backup.command('status', OPERATION),)
    partial_logs = [r for r in caplog.records if 'status=partial_success' in r.getMessage()]
    assert len(partial_logs) == 1
    for record in partial_logs:
        text = record.getMessage()
        assert record.levelno == logging.WARNING
        assert 'reason=partial_receipt_valid' in text
        assert 'action=continue_restart data_loss_risk=true' in text
        assert 'bot_id=b' in text and 'arca_id=ARCA-SANDBOX-p@8' in text
        assert 'env=pre owner_id=o' in text
        assert f'operation_id={OPERATION}' in text
        assert 'generation_id=g' in text
    assert 'phase=prepared status=partial_success' in caplog.text
    assert 'phase=verify status=' not in caplog.text
    assert 'phase=prepare status=failed' not in caplog.text
    assert 'AICODING_RESTART_FAILED' not in caplog.text


@pytest.mark.parametrize('field, value', [
    ('status', 'success'), ('status', None), ('generation_id', None), ('generation_id', ''),
    ('generation_id', 42), ('operation_id', 'b' * 32), ('operation_id', None),
    ('error_code', None), ('error_code', 'archive_failed'), ('failed_phase', None),
    ('failed_phase', 'publish'), ('completeness', 'failed'), ('completeness', 'success'),
])
@pytest.mark.parametrize('delivery', ['start', 'poll'])
def test_invalid_partial_receipt_is_still_failure_under_enabled_guard(field, value, delivery, caplog):
    payload = json.loads(partial_response().stdout)
    if value is None:
        payload['backup'].pop(field, None)
    else:
        payload['backup'][field] = value
    invalid = SimpleNamespace(exit_code=0, stdout=json.dumps(payload))
    results = [invalid] if delivery == 'start' else [response('running'), invalid]
    execute = executor(results)
    with patch.object(backup.time, 'sleep'), pytest.raises(backup.RestartBackupBlocked) as caught:
        backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b',
                               target_id='ARCA-SANDBOX-p@8', blocking_enabled=lambda: True)()
    assert caught.value.reason == 'invalid_receipt'
    assert 'phase=prepare status=failed' in caplog.text
    assert 'AICODING_RESTART_FAILED' in caplog.text


@pytest.mark.parametrize('change, reason', [
    ({'boot_id': 'replacement'}, 'instance_changed'),
    ({'boot_id': None}, 'instance_changed'),
    ({'operation_id': 'b' * 32}, 'operation_mismatch'),
    ({'status': 'failed'}, 'backup_failed'),
])
def test_partial_result_still_fences_operation_and_boot_while_waiting(change, reason):
    payload = json.loads(partial_response().stdout) | change
    execute = executor([response('running'), SimpleNamespace(exit_code=0, stdout=json.dumps(payload))])
    with patch.object(backup.time, 'sleep'), pytest.raises(backup.RestartBackupBlocked) as caught:
        backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b',
                               target_id='ARCA-SANDBOX-p@8', blocking_enabled=lambda: True)
    assert caught.value.reason == reason


def test_partial_logs_do_not_print_raw_diagnostics_or_allow_field_injection(caplog):
    result = partial_response(generation_id='g\nstatus=failed', error_message='private source path or credential')
    execute = executor([result, result])
    backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b',
                           target_id='ARCA-SANDBOX-p@8', blocking_enabled=lambda: True)()
    for record in caplog.records:
        assert '\n' not in record.getMessage()
    assert 'generation_id=g_status_failed' in caplog.text
    assert 'status=failed' not in caplog.text
    assert 'private source' not in caplog.text


def test_full_success_keeps_info_logs_and_is_not_a_partial_warning(caplog):
    caplog.set_level(logging.INFO, logger=backup.logger.name)
    execute = executor([committed(), committed()])
    backup._attempt_backup(execute=execute, operation_id=OPERATION, bot_id='b',
                           target_id='ARCA-SANDBOX-p@8', blocking_enabled=lambda: True)()
    assert 'phase=prepared status=committed' in caplog.text
    assert execute.call_count == 2
    assert 'phase=verify status=' not in caplog.text
    assert 'partial_success' not in caplog.text
    assert 'data_loss_risk=true' not in caplog.text
    assert all(r.levelno == logging.INFO for r in caplog.records)
