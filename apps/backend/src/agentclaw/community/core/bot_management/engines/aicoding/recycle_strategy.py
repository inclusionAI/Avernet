"""Coding-only recycle backup; admission, release and activation are shared."""

from __future__ import annotations

import time
from contextlib import contextmanager

from agentclaw.community.core.bot_management.engines.restart_contract import (
    RecycleBackupBlocked, RecycleTargetChanged,
)
from .restart_state import BackupOperation, BackupPersistenceError, current_backup
from .restart_backup import FASTDISK_TIMEOUT, RestartBackupBlocked
from .restart_arca import RestartBackupTargetChanged


@contextmanager
def _recycle_backup_errors():
    try:
        yield
    except RestartBackupBlocked as error:
        raise RecycleBackupBlocked(error.reason) from error
    except RestartBackupTargetChanged as error:
        raise RecycleTargetChanged("Backup target changed") from error


class AicodingRecycleMixin:
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
