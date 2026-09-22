"""Coding-engine restart precondition; platform exec, never Relay HTTP.

The script is the runtime's v1 lifecycle contract. Only pre-rollout containers
may omit it. The runtime installs it before enabling canonical data bind mounts.
"""
from __future__ import annotations

import json
import shlex
import time
import uuid
from typing import Any, Callable

ENTRY = '/opt/agentclaw/bin/restart_backup'
POLL_SECONDS = 2
# Covers runtime termination (330s) plus archive budget (900s) and exec overhead.
DEADLINE_SECONDS = 1500


def command(action: str, operation: str) -> str:
    # lstat distinguishes ENOENT from EACCES; test -f does not. Inspect the
    # independent mount-upgrade capability before treating absence as legacy.
    code = f'''
import json, os, stat, subprocess
path = {ENTRY!r}
try:
    st = os.lstat(path)
except FileNotFoundError:
    for marker in ('/opt/.aicoding/.fastdisk.ready', '/run/agentclaw-restart-backup/barrier'):
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
        raise RuntimeError('重启备份执行失败；旧沙箱未销毁，请检查容器备份日志')
    try:
        stdout = result.get('stdout') if isinstance(result, dict) else result.stdout
        value = json.loads(stdout.strip())
    except (AttributeError, ValueError, TypeError) as error:
        raise RuntimeError('重启备份结果无效，禁止销毁旧沙箱') from error
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('operation_id') != operation:
        raise RuntimeError('重启备份操作不匹配，禁止销毁旧沙箱')
    if value.get('status') not in {'legacy', 'not_mounted', 'running', 'committed', 'failed'}:
        raise RuntimeError('未知的重启备份状态，禁止销毁旧沙箱')
    return value


def prepare_backup(*, execute: Callable[[str], Any],
                   renew_lease: Callable[[], bool], on_preparing: Callable[[], None],
                   operation_id: str | None = None) -> None:
    operation = operation_id or uuid.uuid4().hex
    deadline = time.monotonic() + DEADLINE_SECONDS
    action = 'start'
    instance_boot = None
    preparing = False

    def require_lease():
        if renew_lease() is not True:
            raise RuntimeError('重启锁已失效，禁止销毁旧沙箱')

    while True:
        require_lease()
        value = parse_result(execute(command(action, operation)), operation)
        status = value['status']
        if status == 'legacy':
            if action != 'start':
                raise RuntimeError('重启备份脚本在执行期间消失')
            require_lease()
            return
        boot = value.get('boot_id')
        if not isinstance(boot, str) or not boot or (instance_boot is not None and instance_boot != boot):
            raise RuntimeError('备份期间实例身份发生变化，禁止销毁')
        instance_boot = boot
        if status == 'not_mounted':
            if action != 'start':
                raise RuntimeError('备份期间挂载状态发生变化')
            require_lease()
            return
        if not preparing:
            on_preparing()
            preparing = True
        if status == 'committed':
            event = value.get('backup')
            if not isinstance(event, dict) or event.get('status') != 'success' or not event.get('generation_id') or event.get('operation_id') != operation:
                raise RuntimeError('缺少有效的最终备份凭据，禁止销毁')
            require_lease()
            return
        if status == 'failed':
            raise RuntimeError('强制备份失败，旧沙箱已保留；请检查 restart backup 日志后重试')
        if time.monotonic() >= deadline:
            raise RuntimeError('强制备份结果尚未确认，旧沙箱已保留；禁止超时放行')
        time.sleep(POLL_SECONDS)
        action = 'status'


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
    return targets


class AicodingRestartBackupMixin:
    def prepare_restart(self, ctx, *, binding_id, device_service, bot_repository, renew_lease,
                        device_id=None, target_runtime=None):
        """One implementation for aicoding/claude_code and all restart entrypoints."""
        if device_id is not None:
            if target_runtime is None:
                raise RuntimeError("Missing published/caller target runtime")
            targets = _live_targets(target_runtime.get_bot(bot_uuid=device_id))
            for physical_id in targets:
                # Pin every command to the same physical container. Bot-level
                # dispatch could otherwise back up a different replica per poll.
                prepare_backup(
                    execute=lambda cmd, target=physical_id: target_runtime.exec_command_on_device(
                        paas_device_id=target, cmd=cmd),
                    operation_id=uuid.uuid5(uuid.NAMESPACE_URL, "restart:" + device_id + ":" + physical_id).hex,
                    renew_lease=renew_lease, on_preparing=lambda: None,
                )
            if targets and set(_live_targets(target_runtime.get_bot(bot_uuid=device_id))) != set(targets):
                raise RuntimeError("备份期间目标容器清单变化，禁止替换")
        elif binding_id is not None:
            def mark_preparing():
                if not bot_repository.update_by_owner(ctx.bot_id, ctx.owner_id, {"status": "PENDING"}):
                    raise RuntimeError("Cannot persist restart backup state")

            try:
                binding = device_service.get_device(binding_id=binding_id)
                target = binding.get('device_id') if isinstance(binding, dict) else getattr(binding, 'device_id', None)
                if not target:
                    raise RuntimeError("无法定位当前旧实例，禁止跳过重启备份")
                prepare_backup(
                    execute=lambda cmd: device_service.exec_shell_new(device_id=target, shell_cmd=cmd),
                    renew_lease=renew_lease, on_preparing=mark_preparing,
                )
            except Exception:
                # Never clear binding; stale workers must not overwrite new state.
                if renew_lease() is True:
                    bot_repository.update_by_owner(ctx.bot_id, ctx.owner_id, {"status": "FAILED"})
                raise
