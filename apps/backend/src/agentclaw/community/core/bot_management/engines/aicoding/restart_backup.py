"""Best-effort coding restart backup; platform exec, never Relay HTTP.

Backup I/O and runtime receipt observations do not veto restart.
Lifecycle binding/Arca target checks, operation ownership and durable mutation
fences remain mandatory. No runtime upgrade is required; the v1 helper's archive budget remains 900 seconds.
"""
from __future__ import annotations

import asyncio
import json
import re
import shlex
import time
import uuid
from typing import Any, Callable
from urllib.parse import quote

from agentclaw.community.core.devices.errors import (
    RestartBackupTargetChanged,
)
from agentclaw.community.log import get_logger

logger = get_logger()

ENTRY = '/opt/agentclaw/bin/restart_backup'
POLL_SECONDS = 2
# Covers runtime termination (330s) plus archive budget (900s) and exec overhead.
DEADLINE_SECONDS = 1500


class RestartBackupError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


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
    if value.get('status') not in {'legacy', 'not_mounted', 'running', 'committed', 'failed'}:
        raise RestartBackupError('unknown_status', '未知的重启备份状态')
    return value


def prepare_backup(*, execute: Callable[[str], Any], operation_id: str,
                   bot_id: str, target_id: str) -> Callable[[], None]:
    """Wait outside the legacy restart lock; return a short receipt verifier."""
    started = time.monotonic()
    # Ordinary durable operations share one budget across task redeliveries and
    # all physical targets. Published/Caller paths retain their existing budget.
    from .restart_state import BACKUP_TIMEOUT, current_restart
    execution = current_restart.get()
    remaining = (BACKUP_TIMEOUT - (time.time() - execution.payload["started_at"])
                 if execution is not None else DEADLINE_SECONDS)
    deadline = started + remaining
    action, boot, last_status = 'start', None, None
    last_log = started

    def log(phase, status, *, level='info', error_type='-', generation='-', reason='-'):
        getattr(logger, level)(
            "event=aicoding_restart_backup phase=%s status=%s bot_id=%s "
            "target_id=%s operation_id=%s elapsed_ms=%s generation_id=%s error_type=%s reason=%s",
            phase, status, bot_id, target_id, operation_id,
            int((time.monotonic() - started) * 1000), generation, error_type, reason,
        )

    def receipt(value):
        event = value.get('backup')
        if (not isinstance(event, dict) or event.get('status') != 'success'
                or not event.get('generation_id') or event.get('operation_id') != operation_id):
            raise RestartBackupError('invalid_receipt', '缺少有效的最终备份凭据')
        return event['generation_id']

    log('probe', 'started')
    try:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("重启备份超过等待预算")
            value = parse_result(execute(command(action, operation_id)), operation_id)
            if time.monotonic() >= deadline:
                raise TimeoutError("重启备份超过等待预算")
            status = value['status']
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
            generation = receipt(value) if status == 'committed' else '-'
            if status in {'legacy', 'not_mounted', 'committed'}:
                log('prepared', status, generation=generation, reason={
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
        raise

    def verify():
        try:
            if execution is not None and time.monotonic() >= deadline:
                raise TimeoutError("重启备份超过等待预算")
            # An absent helper is re-probed read-only; never start work on a
            # replacement container while holding the legacy short-lived lock.
            check_action = 'start' if status == 'not_mounted' else 'status'
            current = parse_result(execute(command(check_action, operation_id)), operation_id)
            if execution is not None and time.monotonic() >= deadline:
                raise TimeoutError("重启备份超过等待预算")
            if current['status'] != status or (boot and current.get('boot_id') != boot):
                raise RestartBackupError('receipt_stale', '备份后实例或挂载状态变化')
            if status == 'committed' and receipt(current) != generation:
                raise RestartBackupError('generation_changed', '备份凭据变化')
            log('verify', 'allowed', generation=generation)
        except Exception as error:
            log('verify', 'failed', level='warning', error_type=type(error).__name__, reason=error_reason(error))
            raise

    return verify


def _best_effort(callback, *, phase, bot_id, target_id, operation_id):
    """Downgrade only backup I/O/result failures, never lifecycle writes/fences.

    No raw exception/output is logged: platform errors can contain credentials.
    None means unconfirmed backup, NOT a successful receipt or empty inventory.
    """
    try:
        return callback()
    except RestartBackupTargetChanged:
        raise
    except Exception as error:
        failure = error
    logger.warning(
        "event=aicoding_restart_backup phase=%s status=degraded action=continue_restart "
        "bot_id=%s target_id=%s operation_id=%s error_type=%s reason=%s "
        "data_loss_risk=true",
        phase, bot_id, target_id, operation_id, type(failure).__name__, error_reason(failure),
    )
    return None


def _attempt_backup(**kwargs):
    identity = {key: kwargs[key] for key in ('bot_id', 'target_id', 'operation_id')}
    verify = _best_effort(lambda: prepare_backup(**kwargs), phase='prepare', **identity)

    def check():
        if verify is not None:
            _best_effort(verify, phase='verify_receipt', **identity)
    return check


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
        "operation_id=%s source=baas_devices elapsed_ms=%s",
        phase, bot_id, target_id, raw_count, status_counts, operation_id, elapsed_ms,
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
        "bot_id=%s target_id=%s operation_id=%s source=baas_devices",
        phase, bot_id, target_id, operation_id,
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
                        "operation_id=%s baas_status=%s target_count=0",
                        phase, bot_id, target_id, operation_id, detail['status'],
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
            "elapsed_ms=%s error_type=%s reason=inventory_query_failed",
            phase, bot_id, target_id, operation_id,
            int((time.monotonic() - started) * 1000), type(error).__name__,
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
            "error_type=%s reason=%s",
            phase, bot_id, target_id, operation_id, type(error).__name__,
            'empty_inventory' if state['devices'] == [] else 'invalid_inventory',
        )
        raise
    logger.info(
        "event=aicoding_restart_backup phase=%s inventory=resolved "
        "bot_id=%s target_id=%s operation_id=%s target_count=%s",
        phase, bot_id, target_id, operation_id, len(targets),
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
    def prepare_restart(self, ctx, *, device_service_provider=None,
                        target_runtime_provider=None, **kwargs):
        """Back up outside the caller's lock; return the under-lock verifier.

        Backup I/O and receipt failures degrade to warnings. Binding reads,
        target checks and the durable CAS fence stay outside that catch boundary;
        their failures still propagate through the original lifecycle.
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
        verify = self._prepare_restart(ctx, target_runtime_provider=target_runtime_provider, **kwargs)
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
        verify = await asyncio.to_thread(self.prepare_restart, ctx, **kwargs)
        if verify is not None:
            await asyncio.to_thread(verify)

    def _prepare_restart(self, ctx, *, binding_id=None, device_service=None,
                        bot_repository=None, device_id=None, target_runtime=None,
                        target_runtime_provider=None, operation_id=None):
        """Only coding engines probe runtime; no generic lifecycle/status changes."""
        try:
            operation_id = _operation_id(operation_id)
            if device_id is not None:
                identity = dict(bot_id=ctx.bot_id, target_id=device_id,
                                operation_id=operation_id)
                targets = _best_effort(
                    lambda: _query_inventory(target_runtime, **identity, phase='resolve'),
                    phase='resolve', **identity,
                )
                if targets == {}:
                    logger.info(
                        "event=aicoding_restart_backup phase=resolve status=skipped "
                        "reason=no_live_targets bot_id=%s target_id=%s operation_id=%s",
                        ctx.bot_id, device_id, operation_id,
                    )
                checks = [_attempt_backup(
                    execute=lambda cmd, target=physical: _execute_physical(target_runtime, target, cmd),
                    operation_id=operation_id, bot_id=ctx.bot_id, target_id=physical,
                ) for physical in (targets or {})]

                def verify():
                    nonlocal targets
                    current_targets = _best_effort(
                        lambda: _query_inventory(target_runtime, **identity, phase='verify'),
                        phase='verify_inventory', **identity,
                    )
                    # Unknown inventory is a backup warning, not evidence that
                    # the target changed. Even an observed change only ends
                    # backup verification; the original restart owns mutation.
                    if (targets is not None and current_targets is not None
                            and set(current_targets) != set(targets)):
                        def changed():
                            raise RestartBackupError('target_changed', '备份期间目标容器清单变化')
                        _best_effort(changed, phase='verify_inventory', **identity)
                        return  # Never probe a changed target with an old receipt.
                    if targets is None and current_targets is not None:
                        # Retain the first valid observation, but never start
                        # backup work on targets discovered during verification.
                        targets = current_targets
                    for check in checks:
                        check()
                return verify
            if binding_id is None:
                logger.info("event=aicoding_restart_backup phase=skip reason=no_binding bot_id=%s", ctx.bot_id)
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
            provider = _field(binding, 'device_provider')
            if provider == 'baas':
                # Existing BaaS inventory/POST contracts also work for FAILED
                # bindings; no change to the ordinary DeviceService exec gate.
                # Resolving the backup-only client is not a restart prerequisite.
                check = _best_effort(
                    lambda: self._prepare_restart(
                        ctx, device_id=target, target_runtime=target_runtime_provider(),
                        operation_id=operation_id),
                    phase='prepare', bot_id=ctx.bot_id, target_id=target,
                    operation_id=operation_id,
                )
            elif provider == 'arca':
                props = _field(binding, 'device_props') or {}
                sandbox_id = props.get('sandbox_id')
                if (execution is not None and 'sandbox_id' in execution.payload
                        and sandbox_id != execution.payload['sandbox_id']):
                    raise RestartBackupTargetChanged('Arca restart target changed')
                if not isinstance(sandbox_id, str) or not sandbox_id:
                    def missing_sandbox():
                        raise RestartBackupError('missing_sandbox', '旧绑定缺少沙箱 ID')
                    _best_effort(missing_sandbox, phase='resolve', bot_id=ctx.bot_id,
                                 target_id=target, operation_id=operation_id)
                    check = None
                else:
                    # Same physical identity as release, not a BaaS inventory or
                    # the logical device_id. Target/DB checks must stay blocking.
                    executor = device_service.prepare_arca_restart_executor(
                        binding_id=binding_id, device_id=target, sandbox_id=sandbox_id,
                    )
                    receipt_check = _attempt_backup(
                        execute=executor.execute, operation_id=operation_id,
                        bot_id=ctx.bot_id, target_id=sandbox_id,
                    )

                    def check():
                        executor.verify()
                        receipt_check()
            else:
                def execute(cmd):
                    if _field(binding, 'status') in {'FAILED', 'STOPPED'}:
                        raise RestartBackupError('missing_device', '无法定位待恢复容器')
                    return device_service.exec_shell_new(device_id=target, shell_cmd=cmd)
                check = _attempt_backup(
                    execute=execute,
                    operation_id=operation_id,
                    bot_id=ctx.bot_id, target_id=target,
                )

            def verify():
                current = bot_repository.get_by_id_and_owner(ctx.bot_id, ctx.owner_id)
                if not isinstance(current, dict) or current.get('binding_id') != binding_id:
                    logger.error("event=aicoding_restart_backup phase=verify status=blocked "
                                 "reason=binding_changed bot_id=%s binding_id=%s", ctx.bot_id, binding_id)
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
            logger.error("event=aicoding_restart_backup phase=precondition status=blocked "
                         "bot_id=%s binding_id=%s target_id=%s error_type=%s reason=%s",
                         ctx.bot_id, binding_id, device_id, type(error).__name__, error_reason(error))
            raise


def _field(record, name):
    return record.get(name) if isinstance(record, dict) else getattr(record, name, None)


def _execute_physical(runtime, target, cmd):
    """Use the existing public POST API; no shared command/recovery API extension."""
    return runtime.post_bots_api(
        path=f"/api/v1/paas/devices/{quote(target, safe='@')}/commands",
        payload={'cmd': cmd}, action='aicoding_restart_backup')
