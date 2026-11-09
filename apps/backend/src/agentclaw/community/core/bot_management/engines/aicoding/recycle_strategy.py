"""Coding recycle policy: reuse restart backup, own async execution and conflicts."""

from __future__ import annotations

import time

from agentclaw.community.core.bot_management.services.resource_mutation import (
    held_resource_lock,
)
from agentclaw.community.utils.env_utils import get_current_env

from .backup_operation import BackupOperation, current_backup
from .recycle_state import assert_not_recycling
from .restart_backup import FASTDISK_TIMEOUT


class AicodingRecycleMixin:
    def execute_recycle(self, ctx, recycle, *, services=None, **kwargs):
        if services is None:
            raise RuntimeError("Coding recycle queue unavailable")
        return services.operations().submit(**kwargs)

    def get_recycle_operation(self, ctx, *, services=None, **kwargs):
        if services is None:
            return None
        return services.operations().get_operation(**kwargs)

    def assert_lifecycle_mutable(self, ctx, bot):
        assert_not_recycling(bot)

    def execute_resource_release(self, ctx, bot, release, *, lifecycle):
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotInvalidLifecycleStateError,
        )

        assert_not_recycling(bot)
        entity = bot.get("entity_id")
        if not entity:
            return release()
        key = (get_current_env(), entity, ctx.bot_id, ctx.owner_id)
        if held_resource_lock.get() == key:
            return release()
        lock = lifecycle._try_acquire_restart_lock(*key)
        if lock is None:
            raise BotInvalidLifecycleStateError(
                bot_id=ctx.bot_id, current_status="BUSY"
            )
        reset_handle = held_resource_lock.set(key)
        try:
            assert_not_recycling(
                lifecycle._repository.get_by_id_and_owner(ctx.bot_id, ctx.owner_id)
                or bot
            )
            return release()
        finally:
            held_resource_lock.reset(reset_handle)
            lifecycle._restart_lock_repo.release(*key[:3], lock.lock_token)

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
            if policy is None:
                policy = save(fail_block=self._fastdisk_backup_blocking())["fail_block"]
            verify = self.prepare_restart(
                ctx,
                **kwargs,
                operation_id=record["operation_id"],
                strict_deadline=time.monotonic()
                + max(0, FASTDISK_TIMEOUT - (time.time() - started)),
                blocking_enabled=lambda: policy,
            )
        finally:
            current_backup.reset(reset_handle)

        def check():
            reset_handle = current_backup.set(operation)
            try:
                if verify is not None:
                    verify()
            finally:
                current_backup.reset(reset_handle)

        return check
