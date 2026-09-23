"""Coding-engine restart precondition; platform exec, never Relay HTTP.

The script is the runtime's v1 lifecycle contract. Only pre-rollout containers
may omit it. The runtime installs it before enabling canonical data bind mounts.
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
        raise RestartBackupError('exec_failed', '重启备份执行失败；旧沙箱未销毁，请检查容器备份日志')
    try:
        stdout = result.get('stdout') if isinstance(result, dict) else result.stdout
        value = json.loads(stdout.strip())
    except (AttributeError, ValueError, TypeError) as error:
        raise RestartBackupError('invalid_response', '重启备份结果无效，禁止销毁旧沙箱') from error
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('operation_id') != operation:
        raise RestartBackupError('operation_mismatch', '重启备份操作不匹配，禁止销毁旧沙箱')
    if value.get('status') not in {'legacy', 'not_mounted', 'running', 'committed', 'failed'}:
        raise RestartBackupError('unknown_status', '未知的重启备份状态，禁止销毁旧沙箱')
    return value


def prepare_backup(*, execute: Callable[[str], Any], operation_id: str,
                   bot_id: str, target_id: str) -> Callable[[], None]:
    """Wait outside the legacy restart lock; return a short receipt verifier."""
    started = time.monotonic()
    deadline = started + DEADLINE_SECONDS
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
            raise RestartBackupError('invalid_receipt', '缺少有效的最终备份凭据，禁止销毁')
        return event['generation_id']

    log('probe', 'started')
    try:
        while True:
            value = parse_result(execute(command(action, operation_id)), operation_id)
            status = value['status']
            if status == 'legacy':
                if action != 'start':
                    raise RestartBackupError('helper_disappeared', '重启备份脚本在执行期间消失')
            else:
                current_boot = value.get('boot_id')
                if not isinstance(current_boot, str) or not current_boot or (boot and boot != current_boot):
                    raise RestartBackupError('instance_changed', '备份期间实例身份发生变化，禁止销毁')
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
                raise RestartBackupError('backup_failed', '强制备份失败，旧沙箱保留；请检查容器备份日志')
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError('强制备份结果未确认，禁止超时放行')
            if status != last_status or now - last_log >= 60:
                log('wait', status)
                last_status, last_log = status, now
            time.sleep(POLL_SECONDS)
            action = 'status'
    except Exception as error:
        # Do not log raw command, stdout/stderr or exception text (may contain credentials).
        log('prepare', 'blocked', level='error', error_type=type(error).__name__, reason=error_reason(error))
        raise

    def verify():
        try:
            # An absent helper is re-probed read-only; never start work on a
            # replacement container while holding the legacy short-lived lock.
            check_action = 'start' if status == 'not_mounted' else 'status'
            current = parse_result(execute(command(check_action, operation_id)), operation_id)
            if current['status'] != status or (boot and current.get('boot_id') != boot):
                raise RestartBackupError('receipt_stale', '备份后实例或挂载状态变化，禁止使用旧凭据重启')
            if status == 'committed' and receipt(current) != generation:
                raise RestartBackupError('generation_changed', '备份凭据变化，禁止销毁')
            log('verify', 'allowed', generation=generation)
        except Exception as error:
            log('verify', 'blocked', level='error', error_type=type(error).__name__, reason=error_reason(error))
            raise

    return verify


def _log_inventory(*, bot_id, target_id, state, phase):
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
        "bot_id=%s target_id=%s raw_device_count=%s raw_status_counts=%s",
        phase, bot_id, target_id, raw_count, status_counts,
    )


def _live_targets(state):
    if not isinstance(state, dict):
        raise RuntimeError("无法确认目标容器状态，禁止替换")
    if state.get("status") == "RELEASED":
        return {}
    devices = state.get("devices")
    if not isinstance(devices, list):
        raise RuntimeError("缺少目标容器清单，禁止替换")
    targets = {}
    for device in devices:
        if not isinstance(device, dict):
            raise RuntimeError("目标容器清单无效")
        # Provider contract: these states mean the container was destroyed.
        if device.get("status") in {"RELEASED", "STOPPED"}:
            continue
        physical_id = device.get("provider_device_id")
        if not isinstance(physical_id, str) or not physical_id:
            raise RuntimeError("无法定位目标物理容器，禁止随机选择实例备份")
        targets[physical_id] = device
    # An explicitly non-empty inventory whose every device is STOPPED/RELEASED
    # is a safe no-op: BaaS has confirmed that there is no live container to
    # enter.  Keep an actually empty inventory fail-closed because it may be a
    # stale or incomplete BaaS projection while the Bot is still present.
    if not devices:
        raise RuntimeError("目标容器清单为空，禁止跳过重启备份")
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

        Self-contained: no lock callbacks. A prepare failure raises before the
        caller ever acquires its lock, so there is nothing for the strategy to
        release. Verification failure propagates through the caller's existing
        error path, which owns lock release.
        """
        if device_service_provider is not None and kwargs.get('binding_id') is not None:
            kwargs['device_service'] = device_service_provider()
        kwargs['operation_id'] = _operation_id(
            kwargs.get('operation_id'), kwargs.pop('restart_key', None)
        )
        return self._prepare_restart(ctx, target_runtime_provider=target_runtime_provider, **kwargs)

    async def prepare_restart_async(self, ctx, **kwargs):
        verify = await asyncio.to_thread(self.prepare_restart, ctx, **kwargs)
        if verify is not None:
            await asyncio.to_thread(verify)

    async def execute_restart(self, ctx, restart, **kwargs):
        # Keep the existing lifecycle callback signature unchanged. The coding
        # precondition generates a fresh operation id in prepare_restart.
        return await asyncio.to_thread(restart, **kwargs)

    def _prepare_restart(self, ctx, *, binding_id=None, device_service=None,
                        bot_repository=None, device_id=None, target_runtime=None,
                        target_runtime_provider=None, operation_id=None):
        """Only coding engines probe runtime; no generic lifecycle/status changes."""
        try:
            operation_id = _operation_id(operation_id)
            if device_id is not None:
                state = target_runtime.get_bot(bot_uuid=device_id)
                _log_inventory(
                    bot_id=ctx.bot_id, target_id=device_id, state=state, phase='resolve'
                )
                targets = _live_targets(state)
                logger.info(
                    "event=aicoding_restart_backup phase=resolve bot_id=%s target_id=%s target_count=%s",
                    ctx.bot_id, device_id, len(targets),
                )

                # BaaS can legitimately report a non-empty inventory whose
                # devices are all STOPPED/RELEASED.  There is no live physical
                # target to back up in that case, so continue with replacement
                # while still re-checking the inventory under the restart lock.
                if not targets:
                    logger.info(
                        "event=aicoding_restart_backup phase=resolve status=skipped "
                        "reason=no_live_targets bot_id=%s target_id=%s",
                        ctx.bot_id, device_id,
                    )

                    def verify_no_live_targets():
                        state = target_runtime.get_bot(bot_uuid=device_id)
                        _log_inventory(
                            bot_id=ctx.bot_id, target_id=device_id, state=state, phase='verify'
                        )
                        if _live_targets(state):
                            logger.error(
                                "event=aicoding_restart_backup phase=verify status=blocked "
                                "reason=target_changed bot_id=%s target_id=%s",
                                ctx.bot_id, device_id,
                            )
                            raise RestartBackupError(
                                'target_changed', '备份期间目标容器清单变化，禁止替换'
                            )
                        logger.info(
                            "event=aicoding_restart_backup phase=verify status=allowed "
                            "reason=no_live_targets bot_id=%s target_id=%s",
                            ctx.bot_id, device_id,
                        )

                    return verify_no_live_targets

                checks = [prepare_backup(
                    execute=lambda cmd, target=physical: _execute_physical(target_runtime, target, cmd),
                    operation_id=operation_id,
                    bot_id=ctx.bot_id, target_id=physical,
                ) for physical in targets]

                def verify():
                    state = target_runtime.get_bot(bot_uuid=device_id)
                    _log_inventory(
                        bot_id=ctx.bot_id, target_id=device_id, state=state, phase='verify'
                    )
                    if set(_live_targets(state)) != set(targets):
                        logger.error("event=aicoding_restart_backup phase=verify status=blocked "
                                     "reason=target_changed bot_id=%s target_id=%s", ctx.bot_id, device_id)
                        raise RestartBackupError('target_changed', '备份期间目标容器清单变化，禁止替换')
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
            provider = _field(binding, 'device_provider')
            if provider == 'baas':
                # Existing BaaS inventory/POST contracts also work for FAILED
                # bindings; no change to the ordinary DeviceService exec gate.
                check = self._prepare_restart(
                    ctx, device_id=target, target_runtime=target_runtime_provider(),
                    operation_id=operation_id)
            else:
                if _field(binding, 'status') in {'FAILED', 'STOPPED'}:
                    # Legacy ARCA's physical sandbox ID is already persisted.
                    # PaaS accepts it independently of OCB's binding status.
                    props = _field(binding, 'device_props') or {}
                    physical = props.get('sandbox_id')
                    if provider != 'arca' or not isinstance(physical, str) or not physical.startswith('ARCA-SANDBOX-'):
                        raise RestartBackupError('missing_device', '无法定位待恢复容器，禁止跳过重启备份')
                    runtime = target_runtime_provider()
                    def execute(cmd):
                        return _execute_physical(runtime, physical, cmd)
                else:
                    def execute(cmd):
                        return device_service.exec_shell_new(device_id=target, shell_cmd=cmd)
                check = prepare_backup(
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
