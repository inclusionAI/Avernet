"""Coding restart backup: confirmed fastdisk is strict only when explicitly enabled.

Backend sends the same read-only detector as publication. Detection failures keep
legacy behavior. When enabled, a confirmed fastdisk target cannot downgrade
after backup failure. Valid partial receipts allow restart with a warning.
Binding checks and durable mutation fences always remain mandatory.
"""
from __future__ import annotations

import asyncio
import json
import re
import shlex
import time
import uuid
import traceback
from contextlib import contextmanager
from copy import deepcopy
from functools import cache
from pathlib import PurePath
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from urllib.parse import quote

from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

from .backup_operation import BackupPersistenceError, current_backup, backup_deadline, backup_operation_type
from .restart_arca import BoundArcaRestartExecutor, RestartBackupTargetChanged
from .restart_dependencies import RestartBackupDependencies
from .fastdisk_probe import detection_command, parse_detection_result
from .restart_state import ENGINES, RestartSuperseded
from ..restart_contract import PublishedRestartServices

logger = get_logger()

MARKER = "AICODING_RESTART_FAILED"


def safe_log_field(value) -> str:
    if not isinstance(value, (str, int)):
        return "-"
    # Keep correlation fields single-line and prevent log-field injection.
    return re.sub(r"[^\w.:@/+-]", "_", str(value))[:256] or "-"


def arca_identity(target_id, sandbox_id=None) -> str:
    """Only a captured sandbox or an ARCA provider ID identifies a container.

    Preserve the full routing identity, including any @tenant suffix, in logs.
    A logical BaaS Bot/device ID must never be advertised as an ARCA ID.
    """
    if isinstance(sandbox_id, str) and sandbox_id:
        return safe_log_field(sandbox_id)
    if isinstance(target_id, str) and target_id.startswith("ARCA-SANDBOX-"):
        return safe_log_field(target_id)
    return "-"


def log_restart_failure(
    error: Exception | None = None, *, bot_id, phase, target_id=None,
    sandbox_id=None, operation_id=None, reason=None, elapsed_ms=None,
    helper_status=None, binding_id=None, publish_id=None, owner_id=None,
    physical_targets=(), backup_failure=False,
) -> None:
    """Emit safe failure evidence, retaining a lower-level physical target.

    Do not print exception messages, source lines, locals, commands or helper
    output: provider exceptions may embed credentials. Chained exception types
    and filename/line/function frames still identify the failure location.
    No provider/database lookup is performed for logging.
    """
    # Losing ownership is a reschedule/no-op, not a failed restart.
    if isinstance(error, RestartSuperseded):
        return
    chain, seen = [], set()
    current = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    details = dict(
        bot_id=bot_id, target_id=target_id,
        arca_id=arca_identity(target_id, sandbox_id), operation_id=operation_id,
        elapsed_ms=elapsed_ms, helper_status=helper_status, owner_id=owner_id,
        env=get_current_env(), physical_targets=tuple(sorted(physical_targets)),
        operation_type=backup_operation_type(), backup_failure=backup_failure,
    )
    # A backup exception may cross inventory/task/publish wrappers. Keep the
    # original physical target rather than overwriting it with a logical ID.
    for cause in reversed(chain):
        captured = getattr(cause, "_coding_restart_log_context", None)
        if isinstance(captured, dict):
            for key, value in captured.items():
                if key in details and value is not None and value not in ("-", "", ()):
                    details[key] = value
    details["backup_failure"] = backup_failure or details["backup_failure"] or any(isinstance(cause, RestartBackupBlocked) for cause in chain)
    if error is not None:
        error.__dict__["_coding_restart_log_context"] = details
    if reason is None:
        reason = getattr(error, "reason", None)
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z_]{1,64}", reason):
            reason = "timeout" if isinstance(error, TimeoutError) else "unexpected_error"
    frames = []
    for cause in chain:
        frames.extend(
            f"{safe_log_field(PurePath(frame.filename).name)}:{frame.lineno}:{safe_log_field(frame.name)}"
            for frame in traceback.extract_tb(cause.__traceback__)[-8:]
        )
    # Inventory-level failures can concern several captured containers. Emit one
    # record per target, without choosing an arbitrary replica or re-querying.
    targets = ([(target, arca_identity(target)) for target in details['physical_targets']]
               if details['arca_id'] == "-" and details['physical_targets']
               else [(details['target_id'], details['arca_id'])])
    for target_id, arca_id in targets:
        (logger.warning if details["backup_failure"] else logger.error)(
            "[%s] event=aicoding_restart_failed status=failed "
            "bot_id=%s arca_id=%s target_id=%s operation_id=%s "
            "phase=%s reason=%s error_type=%s cause_types=%s "
            "elapsed_ms=%s helper_status=%s binding_id=%s publish_id=%s "
            "env=%s owner_id=%s stack=%s operation_type=%s",
            MARKER, safe_log_field(details['bot_id']), safe_log_field(arca_id),
            safe_log_field(target_id), safe_log_field(details['operation_id']),
            safe_log_field(phase), safe_log_field(reason), safe_log_field(type(error).__name__) if error is not None else "-",
            ">".join(safe_log_field(type(cause).__name__) for cause in chain) or "-",
            safe_log_field(details['elapsed_ms']), safe_log_field(details['helper_status']),
            safe_log_field(binding_id), safe_log_field(publish_id), safe_log_field(details['env']),
            safe_log_field(details['owner_id']), "|".join(frames) or "-", details["operation_type"],
        )


ENTRY = '/opt/agentclaw/bin/restart_backup'
POLL_SECONDS = 2
# Covers runtime termination (330s) plus archive budget (900s) and exec overhead.
DEADLINE_SECONDS = 1500
# Confirmed fastdisk: stop waiting after five minutes, never turn timeout into permission.
FASTDISK_TIMEOUT = 300


class RestartBackupError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class RestartBackupBlocked(RestartBackupError):
    """Only a confirmed fastdisk target produces this terminal, sanitized veto."""

    def __init__(self, reason: str):
        detail = {
            'backup_failed': '快盘备份失败',
            'timeout': '快盘备份等待超时，结果未确认',
            'helper_missing': '快盘容器缺少重启备份能力',
            'mount_changed': '快盘挂载状态发生变化',
            'target_changed': '快盘备份目标容器发生变化',
            'warning_unavailable': '快盘备份风险提示保存失败',
        }.get(reason, '快盘备份结果无法确认')
        super().__init__(reason, f'{detail}，本次重启已终止，旧容器未执行销毁或替换')


@dataclass(frozen=True)
class _PreparedBackup:
    required: bool
    verify: Callable[[], None]

    def __call__(self):
        self.verify()


def error_reason(error):
    return getattr(error, 'reason', 'timeout' if isinstance(error, TimeoutError) else 'transport_error')


def command(action: str, operation: str) -> str:
    # lstat distinguishes ENOENT from EACCES; test -f does not. Inspect the
    # rollout-specific installation/barrier before treating absence as legacy.
    # A fastdisk-ready marker alone does NOT prove this backup helper was installed.
    code = f'''
import json, os, stat, subprocess
path = {ENTRY!r}
try:
    st = os.lstat(path)
except FileNotFoundError:
    for marker in ('/opt/agentclaw/restart-backup-v1', '/run/agentclaw-restart-backup/barrier'):
        try:
            os.lstat(marker)
        except FileNotFoundError:
            continue
        raise RuntimeError('restart capability missing on an upgraded container')
    print(json.dumps({{"version": 1, "operation_id": {operation!r}, "status": "legacy"}}))
else:
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022 or not os.access(path, os.X_OK):
        raise RuntimeError('restart capability has invalid ownership or permissions')
    args = [path, {action!r}, {operation!r}]
    if os.geteuid() != 0:
        args = ['sudo', '-n', '--'] + args
    raise SystemExit(subprocess.call(args))
'''
    return 'python3 -c ' + shlex.quote(code)


def parse_result(result: Any, operation: str) -> dict[str, Any]:
    # Never infer success from empty/malformed output or a missing exit code.
    exit_code = result.get('exit_code') if isinstance(result, dict) else getattr(result, 'exit_code', None)
    if exit_code != 0:
        raise RestartBackupError('exec_failed', '重启备份命令执行失败，请检查容器备份日志')
    try:
        stdout = result.get('stdout') if isinstance(result, dict) else result.stdout
        value = json.loads(stdout.strip())
    except (AttributeError, ValueError, TypeError) as error:
        raise RestartBackupError('invalid_response', '重启备份结果无效') from error
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('operation_id') != operation:
        raise RestartBackupError('operation_mismatch', '重启备份操作不匹配')
    if value.get('status') not in {'legacy', 'not_mounted', 'running', 'committed', 'partial_success', 'failed'}:
        raise RestartBackupError('unknown_status', '未知的重启备份状态')
    return value


def prepare_backup(*, execute: Callable[[str], Any], operation_id: str,
                   bot_id: str, target_id: str, require_backup: bool = False,
                   sandbox_id: str | None = None, owner_id: str | None = None,
                   strict_deadline: float | None = None, block_on_failure: bool = True) -> Callable[[], None]:
    """Wait outside the legacy restart lock; return a short receipt verifier."""
    started = time.monotonic()
    from .restart_state import current_restart
    durable_backup = current_restart.get() is not None or current_backup.get() is not None
    deadline = backup_deadline(started=started, ordinary_budget=DEADLINE_SECONDS,
                               fastdisk_budget=FASTDISK_TIMEOUT, required=require_backup,
                               strict_deadline=strict_deadline)
    action, boot, last_status = 'start', None, None
    last_log = started
    observed_status = 'unknown'

    def log(phase, status, *, level='info', error_type='-', generation='-', reason='-'):
        partial = status == 'partial_success'
        getattr(logger, 'warning' if partial else level)(
            "event=aicoding_restart_backup phase=%s status=%s bot_id=%s "
            "target_id=%s operation_id=%s elapsed_ms=%s generation_id=%s error_type=%s reason=%s arca_id=%s env=%s owner_id=%s%s operation_type=%s",
            phase, status, safe_log_field(bot_id), safe_log_field(target_id), operation_id,
            int((time.monotonic() - started) * 1000), safe_log_field(generation), error_type, reason,
            arca_identity(target_id, sandbox_id), get_current_env(), safe_log_field(owner_id),
            " action=continue_restart data_loss_risk=true" if partial else "", backup_operation_type(),
        )

    def receipt(value):
        event = value.get('backup')
        partial = value['status'] == 'partial_success'
        expected_status = 'partial_success' if partial else 'success'
        if (not isinstance(event, dict) or event.get('status') != expected_status
                or not event.get('generation_id') or event.get('operation_id') != operation_id):
            raise RestartBackupError('invalid_receipt', '缺少有效的最终备份凭据')
        completeness = event.get('completeness', 'degraded' if partial else 'success')
        if partial and (event.get('error_code') != 'source_files_skipped'
                        or event.get('failed_phase') != 'backup_partial'
                        or completeness != 'degraded'):
            raise RestartBackupError('invalid_receipt', '缺少有效的部分备份凭据')
        if require_backup or partial:
            if not isinstance(event['generation_id'], str):
                raise RestartBackupError('invalid_receipt', '备份版本无效')
            if completeness not in {'success', 'degraded'}:
                raise RestartBackupError('invalid_receipt', '备份完整性结果无效')
        # A published partial copy counts as success for restart, not as full data.
        return event['generation_id'], partial or completeness == 'degraded'

    log('probe', 'started')
    try:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("重启备份超过等待预算")
            value = parse_result(execute(command(action, operation_id)), operation_id)
            if time.monotonic() >= deadline:
                raise TimeoutError("重启备份超过等待预算")
            status = value['status']
            observed_status = status
            if require_backup and status in {'legacy', 'not_mounted'}:
                reason = 'helper_missing' if status == 'legacy' else 'mount_changed'
                raise RestartBackupError(reason, '已确认快盘，不能跳过重启备份')
            if status == 'legacy':
                if action != 'start':
                    raise RestartBackupError('helper_disappeared', '重启备份脚本在执行期间消失')
            else:
                current_boot = value.get('boot_id')
                if not isinstance(current_boot, str) or not current_boot or (boot and boot != current_boot):
                    raise RestartBackupError('instance_changed', '备份期间实例身份发生变化')
                boot = current_boot
            if status == 'not_mounted' and action != 'start':
                raise RestartBackupError('mount_changed', '备份期间挂载状态发生变化')
            generation, partial = receipt(value) if status in {'committed', 'partial_success'} else ('-', False)
            if status in {'legacy', 'not_mounted', 'committed', 'partial_success'}:
                log('prepared', 'partial_success' if partial else status, generation=generation,
                    reason='partial_receipt_valid' if partial else {
                        'legacy': 'helper_absent', 'not_mounted': 'no_live_binds',
                        'committed': 'receipt_valid'}[status])
                break
            if status == 'failed':
                raise RestartBackupError('backup_failed', '备份脚本报告失败，请检查容器备份日志')
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError('备份等待超时，结果未确认')
            if status != last_status or now - last_log >= 60:
                log('wait', status)
                last_status, last_log = status, now
            time.sleep(POLL_SECONDS)
            action = 'status'
    except Exception as error:
        # Do not log raw command, stdout/stderr or exception text (may contain credentials).
        log('prepare', 'failed', level='warning', error_type=type(error).__name__, reason=error_reason(error))
        if require_backup and block_on_failure:
            log_restart_failure(error, bot_id=bot_id, target_id=target_id, owner_id=owner_id,
                                sandbox_id=sandbox_id, operation_id=operation_id, phase='prepare', backup_failure=True,
                                elapsed_ms=int((time.monotonic() - started) * 1000),
                                helper_status=observed_status)
        raise

    def verify():
        observed_status = 'unknown'
        try:
            if (require_backup or durable_backup) and time.monotonic() >= deadline:
                raise TimeoutError("重启备份超过等待预算")
            # An absent helper is re-probed read-only; never start work on a
            # replacement container while holding the legacy short-lived lock.
            check_action = 'start' if status == 'not_mounted' else 'status'
            current = parse_result(execute(command(check_action, operation_id)), operation_id)
            observed_status = current['status']
            if (require_backup or durable_backup) and time.monotonic() >= deadline:
                raise TimeoutError("重启备份超过等待预算")
            if require_backup and current['status'] == 'failed':
                raise RestartBackupError('backup_failed', '最终快盘备份未成功')
            if current['status'] != status or (boot and current.get('boot_id') != boot):
                raise RestartBackupError('receipt_stale', '备份后实例或挂载状态变化')
            if status in {'committed', 'partial_success'}:
                current_generation, current_partial = receipt(current)
                if current_generation != generation:
                    raise RestartBackupError('generation_changed', '备份凭据变化')
                if current_partial != partial:
                    raise RestartBackupError('receipt_stale', '备份完整性结果变化')
            log('verify', 'partial_success' if partial else 'allowed', generation=generation,
                reason='partial_receipt_valid' if partial else '-')
        except Exception as error:
            log('verify', 'failed', level='warning', error_type=type(error).__name__, reason=error_reason(error))
            if require_backup and block_on_failure:
                log_restart_failure(error, bot_id=bot_id, target_id=target_id, owner_id=owner_id,
                                    sandbox_id=sandbox_id, operation_id=operation_id, phase='verify', backup_failure=True,
                                    elapsed_ms=int((time.monotonic() - started) * 1000),
                                    helper_status=observed_status)
            raise

    return verify


def _best_effort(callback, *, phase, bot_id, target_id, operation_id, sandbox_id=None, owner_id=None, on_degraded=None):
    """Downgrade only backup I/O/result failures, never lifecycle writes/fences.

    No raw exception/output is logged: platform errors can contain credentials.
    None means unconfirmed backup, NOT a successful receipt or empty inventory.
    """
    try:
        return callback()
    except (RestartBackupTargetChanged, RestartBackupBlocked, RestartSuperseded, BackupPersistenceError):
        raise
    except Exception as error:
        failure = error
    logger.warning(
        "event=aicoding_restart_backup phase=%s status=degraded action=continue_restart "
        "bot_id=%s target_id=%s operation_id=%s error_type=%s reason=%s "
        "data_loss_risk=true arca_id=%s env=%s owner_id=%s operation_type=%s",
        phase, bot_id, target_id, operation_id, type(failure).__name__, error_reason(failure),
        arca_identity(target_id, sandbox_id), get_current_env(), safe_log_field(owner_id), backup_operation_type(),
    )
    if on_degraded is not None:
        on_degraded()  # Persistence failures must escape the best-effort catch.
    return None


def _backup_policy(callback, *, required, physical_targets=(), on_degraded=None, **identity):
    if not required:
        return _best_effort(callback, on_degraded=on_degraded, **identity)
    try:
        return callback()
    except (RestartBackupTargetChanged, RestartBackupBlocked, RestartSuperseded, BackupPersistenceError):
        raise
    except Exception as error:
        if not hasattr(error, "_coding_restart_log_context"):
            log_restart_failure(error, physical_targets=physical_targets, backup_failure=True, **identity)
        raise RestartBackupBlocked(error_reason(error)) from error


def _detect_fastdisk(execute, **identity):
    """Failed detection is a compatibility fallback, not a successful probe."""
    def detect():
        from .restart_state import BACKUP_TIMEOUT, current_restart
        execution = current_restart.get()
        backup = current_backup.get()
        if backup is not None and time.time() - backup.started_at >= BACKUP_TIMEOUT:
            raise TimeoutError("detection budget exhausted")
        if execution is not None and time.time() - execution.payload['started_at'] >= BACKUP_TIMEOUT:
            raise TimeoutError('detection budget exhausted')
        return parse_detection_result(execute(detection_command()))['status'] == 'run'

    return _best_effort(detect, phase='detect', **identity) is True


def _confirmed_targets():
    backup = current_backup.get()
    if backup is not None:
        return set(backup.confirmed)
    from .restart_state import current_restart, journal

    execution = current_restart.get()
    if execution is None:
        return set()
    try:
        return set(journal(execution.state.read()).get('fastdisk_targets', []))
    except RestartSuperseded:
        raise
    except Exception as error:
        raise RestartBackupBlocked('confirmation_unavailable') from error


def _attempt_backup(*, blocking_enabled: Callable[[], bool] = lambda: False, **kwargs):
    from .restart_state import current_restart

    identity = {key: kwargs[key] for key in ('bot_id', 'target_id', 'operation_id')}
    identity['sandbox_id'] = kwargs.get('sandbox_id')
    identity['owner_id'] = kwargs.get('owner_id')
    execution = current_restart.get()
    # A redelivery must not forget a previous successful detection. Persistence
    # errors must escape even the outer BaaS compatibility catch.
    known = _confirmed_targets()
    fastdisk = identity['target_id'] in known or _detect_fastdisk(kwargs['execute'], **identity)
    required = fastdisk and blocking_enabled()
    backup = current_backup.get()
    if fastdisk and backup is not None:
        backup.confirm(identity["target_id"])
    def warn():
        if backup is not None:
            backup.warn()
            return
        from .restart_state import record_nonblocking_backup_warning
        try:
            record_nonblocking_backup_warning(identity['operation_id'])
        except RestartSuperseded:
            raise
        except Exception as error:
            raise RestartBackupBlocked('warning_unavailable') from error
    on_degraded = warn if fastdisk and not required else None
    if required and execution is not None and identity['target_id'] not in known:
        try:
            execution.state.update({'BACKING_UP'}, fastdisk_targets=[identity['target_id']])
        except RestartSuperseded:
            raise
        except Exception as error:
            raise RestartBackupBlocked('confirmation_unavailable') from error
    verify = _backup_policy(
        lambda: prepare_backup(**kwargs, require_backup=fastdisk, block_on_failure=required),
        required=required, phase='prepare', on_degraded=on_degraded, **identity,
    )

    def check():
        if verify is not None:
            _backup_policy(verify, required=required, phase='verify_receipt', on_degraded=on_degraded, **identity)
    return _PreparedBackup(required=required, verify=check)


def _log_inventory(*, bot_id, target_id, state, phase, operation_id="-", elapsed_ms=0):
    """Log a redacted BaaS inventory summary before target resolution."""
    if not isinstance(state, dict):
        raw_count = -1
        status_counts = {'<invalid_state>': 1}
    else:
        devices = state.get('devices')
        if not isinstance(devices, list):
            raw_count = -1
            status_counts = {'<missing_or_invalid>': 1}
        else:
            raw_count = len(devices)
            status_counts = {}
            for device in devices:
                status = device.get('status') if isinstance(device, dict) else '<invalid_device>'
                status = str(status) if status is not None else '<missing>'
                status_counts[status] = status_counts.get(status, 0) + 1
    logger.info(
        "event=aicoding_restart_backup phase=%s inventory=observed "
        "bot_id=%s target_id=%s raw_device_count=%s raw_status_counts=%s "
        "operation_id=%s source=baas_devices elapsed_ms=%s operation_type=%s",
        phase, bot_id, target_id, raw_count, status_counts, operation_id, elapsed_ms, backup_operation_type(),
    )


def _live_targets(state):
    if not isinstance(state, dict):
        raise RestartBackupError("invalid_inventory", "无法确认备份目标容器状态")
    if state.get("status") == "RELEASED":
        return {}
    devices = state.get("devices")
    if not isinstance(devices, list):
        raise RestartBackupError("invalid_inventory", "缺少有效的备份目标容器清单")
    targets = {}
    for device in devices:
        if not isinstance(device, dict):
            raise RestartBackupError("invalid_inventory", "备份目标容器清单无效")
        # Provider contract: these states mean the container was destroyed.
        if device.get("status") in {"RELEASED", "STOPPED"}:
            continue
        physical_id = device.get("provider_device_id")
        if not isinstance(physical_id, str) or not physical_id:
            raise RestartBackupError("invalid_inventory", "无法定位备份目标物理容器")
        targets[physical_id] = device
    # An explicitly non-empty inventory whose every device is STOPPED/RELEASED
    # is a safe no-op: BaaS has confirmed that there is no live container to
    # enter. An actually empty inventory remains unconfirmed rather than proof
    # of release. The caller logs a warning and continues the original restart.
    if not devices:
        raise RestartBackupError("empty_inventory", "备份目标容器清单为空")
    return targets


def _query_inventory(runtime, *, bot_id, target_id, operation_id, phase):
    """Coding-only inventory lookup; keep shared BaaS lifecycle untouched."""
    started = time.monotonic()
    logger.info(
        "event=aicoding_restart_backup phase=%s inventory=query_started "
        "bot_id=%s target_id=%s operation_id=%s source=baas_devices operation_type=%s",
        phase, bot_id, target_id, operation_id, backup_operation_type(),
    )
    try:
        state = {'devices': runtime.list_devices_by_bot_uuid(bot_uuid=target_id)}
        if state['devices'] == []:
            # An empty projection alone is not proof that replacement is safe.
            # Consult BaaS, not the local Bot status (durable admission already
            # changed that to PENDING). Never skip backup for a FAILED Bot that
            # still has devices. get_bot's devices field is not an inventory
            # without health_check, so re-read the dedicated devices endpoint.
            detail = runtime.get_bot(bot_uuid=target_id)
            if isinstance(detail, dict) and (
                (detail.get('status') == 'RELEASED'
                 and detail.get('bot_uuid', target_id) == target_id)
                or (detail.get('status') == 'FAILED'
                    and detail.get('bot_uuid') == target_id)
            ):
                state['devices'] = runtime.list_devices_by_bot_uuid(bot_uuid=target_id)
                if state['devices'] == []:
                    _log_inventory(
                        bot_id=bot_id, target_id=target_id, state=state, phase=phase,
                        operation_id=operation_id,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
                    logger.info(
                        "event=aicoding_restart_backup phase=%s inventory=resolved "
                        "reason=confirmed_empty_terminal_bot bot_id=%s target_id=%s "
                        "operation_id=%s baas_status=%s target_count=0 operation_type=%s",
                        phase, bot_id, target_id, operation_id, detail['status'], backup_operation_type(),
                    )
                    return {}
                if isinstance(state['devices'], list) and state['devices']:
                    # The initial observation had no devices. A newly visible
                    # target may belong to another in-flight reconstruction;
                    # never start a backup on it under this recovery attempt.
                    raise RestartBackupError(
                        'target_changed', '确认期间目标容器清单变化'
                    )
    except Exception as error:
        # Shared transport exceptions may contain response bodies/credentials.
        logger.warning(
            "event=aicoding_restart_backup phase=%s inventory=query_failed "
            "bot_id=%s target_id=%s operation_id=%s source=baas_devices "
            "elapsed_ms=%s error_type=%s reason=inventory_query_failed operation_type=%s",
            phase, bot_id, target_id, operation_id,
            int((time.monotonic() - started) * 1000), type(error).__name__, backup_operation_type(),
        )
        raise
    _log_inventory(
        bot_id=bot_id, target_id=target_id, state=state, phase=phase,
        operation_id=operation_id, elapsed_ms=int((time.monotonic() - started) * 1000),
    )
    try:
        targets = _live_targets(state)
    except Exception as error:
        logger.warning(
            "event=aicoding_restart_backup phase=%s inventory=unavailable "
            "bot_id=%s target_id=%s operation_id=%s source=baas_devices "
            "error_type=%s reason=%s operation_type=%s",
            phase, bot_id, target_id, operation_id, type(error).__name__,
            'empty_inventory' if state['devices'] == [] else 'invalid_inventory', backup_operation_type(),
        )
        raise
    logger.info(
        "event=aicoding_restart_backup phase=%s inventory=resolved "
        "bot_id=%s target_id=%s operation_id=%s target_count=%s operation_type=%s",
        phase, bot_id, target_id, operation_id, len(targets), backup_operation_type(),
    )
    return targets


def _operation_id(value, restart_key=None):
    if value is None:
        return (uuid.uuid5(uuid.NAMESPACE_URL, restart_key).hex
                if restart_key is not None else uuid.uuid4().hex)
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('operation_id must be a 32-character lowercase hex string')
    return value


class AicodingRestartBackupMixin:
    def get_restart_status(self, *, ext, published=False, progress=None, public=False):
        from .restart_state import restart_status
        return restart_status(ext, published=published, progress=progress, public=public)

    def project_status_ext(self, *, ext, response_ext, published=False, progress=None, public=False):
        from .restart_state import project_status_ext
        return project_status_ext(ext, response_ext=response_ext, published=published,
                                  progress=progress, public=public)

    def published_restart_result(self, *, record):
        from .restart_state import published_restart_result
        return published_restart_result(record)

    def observe_published_restart(self, *, record, progress, mutate_ext):
        result = self.get_restart_status(ext=record.ext or {}, published=True, progress=progress)
        if result is None or result['status'] not in {'SUCCEEDED', 'FAILED'}:
            return
        def complete(ext):
            state = ext.get('restart') or {}
            if (state.get('operation_id') == result['operation_id']
                    and state.get('phase') == 'WAITING_READY'):
                state.update(status=result['status'], message=result['message'])
        mutate_ext(publish_id=record.id, mutator=complete)

    def accept_published_restart(self, ctx, *, services, publish_id, stage):
        from ..restart_contract import RestartAdmission
        operation_id = uuid.uuid4().hex
        def accept(ext):
            restart = ext.setdefault('restart', {})
            restart.update(engine=ctx.active_engine, operation_id=operation_id,
                           stage=stage, status='RUNNING', phase='QUEUED', message=None)
            restart.pop('backup_warning', None)
            restart.pop('precondition_error', None)
            restart.pop('restarting', None)
            restart.pop('guard', None)
        ext = services.mutate_ext(publish_id=publish_id, mutator=accept)
        return RestartAdmission(
            operation_id=operation_id,
            ext=self.project_status_ext(ext=ext, response_ext={}, published=True, public=True),
        )

    @contextmanager
    def published_restart_scope(
        self, ctx, *, services: PublishedRestartServices,
        publish_id: int, stage: str, binding_id: int, source_status: str,
        operation_id: str | None = None,
    ) -> Iterator[dict]:
        if str(ctx.active_engine or "").strip().lower() not in ENGINES:
            yield {}
            return

        record = services.get_record(publish_id)
        if record is None:
            raise RuntimeError(f"Publish record not found: {publish_id}")
        blocked = ((record.ext or {}).get("restart") or {}).get("precondition_error") or {}
        previous = (record.ext or {}).get('restart') or {}
        if (operation_id and previous.get('operation_id') != operation_id
                and previous.get('phase') == 'QUEUED'):
            yield {'success': False, 'message': '本次重启已被更新的请求替代'}
            return
        if (blocked.get('stage') == stage and (
                operation_id is None or previous.get('operation_id') == operation_id)):
            # A queue redelivery must not forget a terminal backup veto.
            yield {"success": False, "message": blocked["message"]}
            return

        from .restart_state import published_backup_warning, published_warning_reporter
        guard_id = uuid.uuid4().hex

        def claim(ext: dict) -> None:
            state = ext.setdefault("restart", {})
            if state.get('operation_id') != (operation_id or guard_id):
                state.pop('backup_warning', None)
            state["guard"] = {
                "id": guard_id, "binding_id": binding_id, "status": source_status,
            }
            state.update(engine=ctx.active_engine, operation_id=operation_id or guard_id,
                         stage=stage, status='RUNNING', phase='BACKING_UP', message=None)
            state.pop('failure_phase', None)
            state.pop("precondition_error", None)

        services.mutate_ext(publish_id=publish_id, mutator=claim)
        warning_scope = published_backup_warning.set(published_warning_reporter(
            services, publish_id=publish_id, guard_id=guard_id,
        ))
        outcome = {}
        try:
            yield outcome
            def submitted(ext):
                state = ext.get('restart') or {}
                if (state.get('guard') or {}).get('id') == guard_id:
                    state['phase'] = 'WAITING_READY'
            services.mutate_ext(publish_id=publish_id, mutator=submitted)
        except RestartBackupBlocked as error:
            log_restart_failure(error, bot_id=ctx.bot_id, phase="published_restart", owner_id=ctx.owner_id,
                                publish_id=publish_id, binding_id=binding_id)
            self._persist_published_restart_veto(
                services, publish_id=publish_id, stage=stage,
                guard_id=guard_id, message=str(error),
            )
            outcome.update(success=False, message=str(error))
        finally:
            published_backup_warning.reset(warning_scope)

    @staticmethod
    def _persist_published_restart_veto(
        services: PublishedRestartServices, *, publish_id: int,
        stage: str, guard_id: str, message: str,
    ) -> None:
        # Import lazily: service_bot package initialization consumes the engine registry.
        from agentclaw.community.core.service_bot.repository.models import PublishStatus

        record = services.get_record(publish_id)
        if record is None:
            raise RuntimeError(f"Publish record not found: {publish_id}")
        expected_ext = deepcopy(record.ext)
        ext = deepcopy(record.ext or {})
        restart = ext.get("restart") or {}
        guard = restart.get("guard") or {}
        if (guard.get("id") != guard_id
                or guard.get("status") != record.status
                or (ext.get("binding") or {}).get(stage) != guard.get("binding_id")):
            return  # A concurrent restart/release owns the state now.
        restart.pop("restarting", None)
        restart.pop("guard", None)
        restart["precondition_error"] = {"stage": stage, "message": message}
        restart.update(status='FAILED', phase='BACKING_UP', message=message)
        ext.pop("retry", None)
        # Persistence/CAS errors propagate: never claim the failure was saved.
        services.update_status(
            publish_id=publish_id, target_status=PublishStatus(record.status),
            source_status=PublishStatus(record.status), ext=ext,
            expected_ext=expected_ext,
        )

    async def acquire_restart_workflow(self, ctx, op, issue, *, runner):
        if str(ctx.active_engine or "").strip().lower() not in ENGINES:
            return await runner.acquire_workflow(op, issue)

        async def guarded_issue():
            try:
                return await issue()
            except RestartBackupBlocked as error:
                # Close the exact op we were given, never a queried latest op.
                # No workflow was issued. Transient/post-submit errors are not
                # classified here and retain the runner's adoption semantics.
                runner.fail_operation(op, str(error))
                raise

        return await runner.acquire_workflow(op, guarded_issue)

    def _fastdisk_backup_blocking(self) -> bool:
        """Only an explicit enabled boolean true may block on backup failure."""
        if self._common_config_provider is None:
            return False
        try:
            value = self._common_config_provider().get_value(
                business_code="aicoding", param_code="fastdisk_fail_block",
                env=get_current_env(), default=False, only_enabled=True,
            )
        except Exception as error:
            logger.warning(
                "event=aicoding_restart_backup phase=config reason=config_unavailable "
                "action=keep_nonblocking env=%s error_type=%s operation_type=%s",
                get_current_env(), type(error).__name__, backup_operation_type(),
            )
            return False
        return value is True

    def prepare_restart(self, ctx, *, device_service_provider=None, **kwargs):
        """Back up outside the caller's lock; return the under-lock verifier.

        Non-fastdisk, unknown detection and explicitly disabled guards warn and continue.
        Enabled fastdisk vetoes, binding reads and the durable CAS fence always
        propagate through the original lifecycle.
        """
        from .restart_state import current_restart
        execution = current_restart.get()
        if execution is not None:
            execution.check_target(ctx, kwargs.get('binding_id'))
            kwargs['operation_id'] = execution.operation_id
        if device_service_provider is not None and kwargs.get('binding_id') is not None:
            kwargs['device_service'] = device_service_provider()
        kwargs['operation_id'] = _operation_id(
            kwargs.get('operation_id'), kwargs.pop('restart_key', None)
        )
        verify = self._prepare_restart(ctx, **kwargs)
        if execution is None:
            return verify

        def verify_and_fence():
            verify()
            execution.verify_backup = verify
            # BaaS request preparation is not a remote mutation. Its fence is
            # installed by before_restart_submission, immediately before POST.
            if execution.payload["provider"] != "baas":
                execution.fence_mutation()
        return verify_and_fence

    async def prepare_restart_async(self, ctx, **kwargs):
        if 'target_runtime' in kwargs:
            kwargs['restart_dependencies'] = RestartBackupDependencies.for_instance(
                kwargs.pop('target_runtime')
            )
        verify = await asyncio.to_thread(self.prepare_restart, ctx, **kwargs)
        if verify is not None:
            await asyncio.to_thread(verify)

    def _prepare_restart(self, ctx, *, binding_id=None, device_service=None,
                        bot_repository=None, device_id=None,
                        operation_id=None, restart_dependencies: RestartBackupDependencies | None = None,
                        strict_deadline: float | None = None,
                        blocking_enabled: Callable[[], bool] | None = None):
        """Only coding engines probe runtime; no generic lifecycle/status changes."""
        log_target, log_sandbox, log_targets = device_id, None, ()
        try:
            operation_id = _operation_id(operation_id)
            dependencies = restart_dependencies or RestartBackupDependencies()
            if blocking_enabled is None:
                blocking_enabled = cache(self._fastdisk_backup_blocking)
            if strict_deadline is None:
                strict_deadline = time.monotonic() + FASTDISK_TIMEOUT
            if device_id is not None:
                target_runtime = None
                identity = dict(bot_id=ctx.bot_id, target_id=device_id,
                                operation_id=operation_id)
                def inventory():
                    nonlocal target_runtime
                    if target_runtime is None:
                        target_runtime = dependencies.baas_runtime()
                    return _query_inventory(target_runtime, **identity, phase='resolve')

                confirmed = _confirmed_targets()
                log_targets = confirmed
                targets = _backup_policy(
                    inventory, required=bool(confirmed) and blocking_enabled(), phase='resolve', **identity,
                    owner_id=ctx.owner_id, physical_targets=confirmed,
                )
                if confirmed and blocking_enabled() and not confirmed.issubset(targets or {}):
                    raise RestartBackupBlocked('target_changed')
                log_targets = tuple(targets or confirmed)
                if targets == {}:
                    logger.info(
                        "event=aicoding_restart_backup phase=resolve status=skipped "
                        "reason=no_live_targets bot_id=%s target_id=%s operation_id=%s operation_type=%s",
                        ctx.bot_id, device_id, operation_id, backup_operation_type(),
                    )
                checks = [_attempt_backup(
                    execute=lambda cmd, target=physical: _execute_physical(target_runtime, target, cmd),
                    operation_id=operation_id, bot_id=ctx.bot_id, target_id=physical, owner_id=ctx.owner_id,
                    strict_deadline=strict_deadline, blocking_enabled=blocking_enabled,
                ) for physical in (targets or {})]

                required = any(check.required for check in checks)

                def verify():
                    nonlocal targets
                    current_targets = _backup_policy(
                        lambda: _query_inventory(target_runtime, **identity, phase='verify'),
                        required=required, phase='verify_inventory', **identity,
                        owner_id=ctx.owner_id, physical_targets=log_targets,
                    )
                    # Once any target needs a durable fastdisk receipt, an
                    # unknown/changed inventory can no longer authorize mutation.
                    if (targets is not None and current_targets is not None
                            and set(current_targets) != set(targets)):
                        def changed():
                            raise RestartBackupError('target_changed', '备份期间目标容器清单变化')
                        _backup_policy(changed, required=required, phase='verify_inventory', **identity,
                                       owner_id=ctx.owner_id, physical_targets=log_targets)
                        return  # Never probe a changed target with an old receipt.
                    if targets is None and current_targets is not None:
                        # Retain the first valid observation, but never start
                        # backup work on targets discovered during verification.
                        targets = current_targets
                    for check in checks:
                        check()
                return verify
            if binding_id is None:
                logger.info("event=aicoding_restart_backup phase=skip reason=no_binding bot_id=%s operation_type=%s", ctx.bot_id, backup_operation_type())
                return lambda: None
            binding = device_service.get_device(binding_id=binding_id)
            target = binding.get('device_id') if isinstance(binding, dict) else getattr(binding, 'device_id', None)
            if not isinstance(target, str) or not target:
                raise RestartBackupError('missing_device', '无法定位当前旧实例，禁止跳过重启备份')
            from .restart_state import current_restart
            execution = current_restart.get()
            if execution is not None and (
                target != execution.payload['device_id']
                or _field(binding, 'device_provider') != execution.payload['provider']
            ):
                raise RestartBackupError('target_changed', '目标容器已变化，禁止替换')
            log_target = target
            provider = _field(binding, 'device_provider')
            if provider == 'baas':
                # Existing BaaS inventory/POST contracts also work for FAILED
                # bindings; no change to the ordinary DeviceService exec gate.
                # Resolving the backup-only client is not a restart prerequisite.
                check = _backup_policy(
                    lambda: self._prepare_restart(
                        ctx, device_id=target,
                        restart_dependencies=RestartBackupDependencies.for_instance(dependencies.baas_runtime()),
                        operation_id=operation_id, strict_deadline=strict_deadline, blocking_enabled=blocking_enabled),
                    required=bool(_confirmed_targets()) and blocking_enabled(),
                    phase='prepare', bot_id=ctx.bot_id, target_id=target, owner_id=ctx.owner_id,
                    operation_id=operation_id,
                )
            elif provider == 'arca':
                props = _field(binding, 'device_props') or {}
                sandbox_id = props.get('sandbox_id')
                log_sandbox = sandbox_id
                if (execution is not None and 'sandbox_id' in execution.payload
                        and sandbox_id != execution.payload['sandbox_id']):
                    raise RestartBackupTargetChanged('Arca restart target changed')
                if not isinstance(sandbox_id, str) or not sandbox_id:
                    def missing_sandbox():
                        raise RestartBackupError('missing_sandbox', '旧绑定缺少沙箱 ID')
                    _best_effort(missing_sandbox, phase='resolve', bot_id=ctx.bot_id, owner_id=ctx.owner_id,
                                 target_id=target, operation_id=operation_id)
                    check = None
                else:
                    # Same physical identity as release, not a BaaS inventory or
                    # the logical device_id. Target/DB checks must stay blocking.
                    transport = dependencies.arca_transport()
                    executor = BoundArcaRestartExecutor(
                        bindings=device_service, transport=transport,
                        binding_id=binding_id,
                        device_id=target, sandbox_id=sandbox_id,
                    )
                    receipt_check = _attempt_backup(
                        execute=executor.execute, operation_id=operation_id,
                        bot_id=ctx.bot_id, target_id=sandbox_id, sandbox_id=sandbox_id, owner_id=ctx.owner_id,
                        strict_deadline=strict_deadline, blocking_enabled=blocking_enabled,
                    )

                    def check():
                        try:
                            executor.verify()
                            receipt_check()
                        except Exception as error:
                            log_restart_failure(error, bot_id=ctx.bot_id, target_id=sandbox_id, owner_id=ctx.owner_id,
                                                sandbox_id=sandbox_id, operation_id=operation_id,
                                                binding_id=binding_id, phase='verify_target')
                            raise
            else:
                def execute(cmd):
                    if _field(binding, 'status') in {'FAILED', 'STOPPED'}:
                        raise RestartBackupError('missing_device', '无法定位待恢复容器')
                    return device_service.exec_shell_new(device_id=target, shell_cmd=cmd)
                check = _attempt_backup(
                    execute=execute,
                    operation_id=operation_id,
                    bot_id=ctx.bot_id, target_id=target, strict_deadline=strict_deadline, owner_id=ctx.owner_id,
                    blocking_enabled=blocking_enabled,
                )

            def verify():
                current = bot_repository.get_by_id_and_owner(ctx.bot_id, ctx.owner_id)
                if not isinstance(current, dict) or current.get('binding_id') != binding_id:
                    logger.error("event=aicoding_restart_backup phase=verify status=blocked "
                                 "reason=binding_changed bot_id=%s binding_id=%s operation_type=%s", ctx.bot_id, binding_id, backup_operation_type())
                    raise RestartBackupError('binding_changed', '备份后绑定已变化，禁止替换其他容器')
                if provider == 'arca' and check is None:
                    current_binding = device_service.get_device(binding_id=binding_id)
                    if (
                        _field(current_binding, 'device_id') != target
                        or _field(current_binding, 'device_provider') != provider
                        or _field(current_binding, 'status') not in {'ACTIVE', 'PENDING', 'FAILED', 'STOPPED'}
                        or (_field(current_binding, 'device_props') or {}).get('sandbox_id') != sandbox_id
                    ):
                        raise RestartBackupTargetChanged('Arca restart target changed')
                if check is not None:
                    check()
            return verify
        except Exception as error:
            log_restart_failure(error, bot_id=ctx.bot_id, target_id=log_target, owner_id=ctx.owner_id,
                                physical_targets=log_targets,
                                sandbox_id=log_sandbox, operation_id=operation_id,
                                binding_id=binding_id, phase="precondition")
            (logger.warning if isinstance(error, RestartBackupBlocked) else logger.error)("event=aicoding_restart_backup phase=precondition status=blocked "
                         "bot_id=%s binding_id=%s target_id=%s error_type=%s reason=%s operation_type=%s",
                         ctx.bot_id, binding_id, device_id, type(error).__name__, error_reason(error), backup_operation_type())
            raise


def _field(record, name):
    return record.get(name) if isinstance(record, dict) else getattr(record, name, None)


def _execute_physical(runtime, target, cmd):
    """Use the existing public POST API; no shared command/recovery API extension."""
    return runtime.post_bots_api(
        path=f"/api/v1/paas/devices/{quote(target, safe='@')}/commands",
        payload={'cmd': cmd}, action='aicoding_restart_backup')
