"""Coding-only recycle backup; admission, release and activation are shared."""

from __future__ import annotations

import time
from contextlib import contextmanager

from agentclaw.community.core.bot_management.engines.restart_contract import (
    RecycleBackupBlocked, RecycleTargetChanged,
)
from agentclaw.community.log import get_logger

from . import restart_backup
from .restart_arca import BoundArcaRestartExecutor, RestartBackupTargetChanged
from .restart_backup import (
    FASTDISK_TIMEOUT,
    RestartBackupBlocked,
    RestartBackupError,
    _execute_physical,
    _field,
    _query_inventory,
    safe_log_field,
)
from .restart_state import BackupOperation, BackupPersistenceError, current_backup

logger = get_logger()


@contextmanager
def _recycle_backup_errors():
    try:
        yield
    except RestartBackupBlocked as error:
        raise RecycleBackupBlocked(error.reason) from error
    except RestartBackupTargetChanged as error:
        raise RecycleTargetChanged("Backup target changed") from error


class AicodingRecycleMixin:
    def should_async_recycle(
        self,
        ctx,
        *,
        binding_id=None,
        device_service_provider=None,
        restart_dependencies=None,
        operation_id=None,
        **_kwargs,
    ):
        """Probe the current target without starting the backup helper or reading policy.

        Detection failures intentionally return False: recycle then keeps the legacy
        synchronous path, matching the restart strategy's NAS/unknown fallback.
        """
        if binding_id is None or device_service_provider is None:
            return False
        try:
            from .restart_dependencies import RestartBackupDependencies

            dependencies = restart_dependencies or RestartBackupDependencies()
            devices = device_service_provider()
            binding = devices.get_device(binding_id=binding_id)
            if binding is None:
                return False
            target = _field(binding, "device_id")
            provider = _field(binding, "device_provider")
            if not isinstance(target, str) or not target:
                return False
            operation_id = operation_id or "recycle-admission"
            identity = dict(
                bot_id=ctx.bot_id,
                target_id=target,
                operation_id=operation_id,
                owner_id=ctx.owner_id,
            )
            if provider == "baas":
                runtime = dependencies.baas_runtime()
                targets = (
                    _query_inventory(
                        runtime,
                        bot_id=ctx.bot_id,
                        target_id=target,
                        operation_id=operation_id,
                        phase="recycle_admission",
                    )
                    or {}
                )
                return any(
                    restart_backup._detect_fastdisk(
                        lambda cmd, physical=physical: _execute_physical(
                            runtime, physical, cmd
                        ),
                        **{**identity, "target_id": physical},
                    )
                    for physical in targets
                )
            if provider == "arca":
                sandbox_id = (_field(binding, "device_props") or {}).get("sandbox_id")
                if not isinstance(sandbox_id, str) or not sandbox_id:
                    return False
                executor = BoundArcaRestartExecutor(
                    bindings=devices,
                    transport=dependencies.arca_transport(),
                    binding_id=binding_id,
                    device_id=target,
                    sandbox_id=sandbox_id,
                )
                return restart_backup._detect_fastdisk(
                    executor.execute,
                    **{**identity, "target_id": sandbox_id, "sandbox_id": sandbox_id},
                )

            def execute(command_text):
                if _field(binding, "status") in {"FAILED", "STOPPED"}:
                    raise RestartBackupError("missing_device", "无法定位待回收容器")
                return devices.exec_shell_new(device_id=target, shell_cmd=command_text)

            return restart_backup._detect_fastdisk(execute, **identity)
        except Exception as error:
            logger.warning(
                "event=aicoding_restart_fastdisk_detect phase=recycle_admission "
                "status=unknown bot_id=%s error_type=%s operation_type=recycle",
                safe_log_field(ctx.bot_id),
                type(error).__name__,
            )
            return False

    def prepare_recycle(self, ctx, *, record, save, **kwargs):
        """Same config, budget and receipt policy as restart, no restart mutation."""
        started = record["backup_started_at"]
        operation = BackupOperation(
            operation_id=record["operation_id"],
            started_at=started,
            confirmed=set(record.get("fastdisk_targets") or []),
            persist=save,
        )
        reset_handle = current_backup.set(operation)
        try:
            policy = record.get("fail_block")

            def blocking_enabled():
                nonlocal policy
                if policy is None:
                    try:
                        policy = save(fail_block=self._fastdisk_backup_blocking())["fail_block"]
                    except Exception as error:
                        raise BackupPersistenceError("Backup policy could not be saved") from error
                return policy

            with _recycle_backup_errors():
                verify = self.prepare_restart(
                    ctx,
                    **kwargs,
                    operation_id=record["operation_id"],
                    strict_deadline=time.monotonic()
                    + max(0, FASTDISK_TIMEOUT - (time.time() - started)),
                    blocking_enabled=blocking_enabled,
                )
        finally:
            current_backup.reset(reset_handle)

        def check():
            reset_handle = current_backup.set(operation)
            try:
                with _recycle_backup_errors():
                    if verify is not None:
                        verify()
            finally:
                current_backup.reset(reset_handle)

        return check
