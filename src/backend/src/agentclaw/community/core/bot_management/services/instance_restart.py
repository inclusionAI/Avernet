"""Instance lifecycle adapter: engine policy owns backup, caller owns replacement.

The target lock spans precondition AND replacement submission. No source Bot
status/binding is changed by a published/caller restart.
"""
from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager, suppress

from agentclaw.community.core.bot_management.engines import resolve_provisioning
from agentclaw.community.utils.env_utils import get_current_env


class InstanceRestartMixin:
    @asynccontextmanager
    async def instance_restart_guard(self, *, bot: dict, device_id: str):
        if not isinstance(device_id, str) or not device_id:
            raise ValueError("Restart requires the actual old instance device_id")
        owner = str(bot.get("owner_id") or bot.get("entity_id") or "")
        ctx, strategy = resolve_provisioning(
            bot_id=str(bot.get("bot_id") or ""), owner_id=owner,
            bot_type=str(bot.get("bot_type") or "service"),
            active_engine=bot.get("active_engine") or bot.get("engine_type"),
            template_type=bot.get("template_type"), template_config=bot.get("template_config"),
        )
        # Separate from source-bot locks, shared across entrypoints to this target.
        target_key = "instance:" + hashlib.sha256(device_id.encode()).hexdigest()[:32]
        env = get_current_env()
        lock = self._try_acquire_restart_lock(env, owner, target_key, owner)
        if lock is None:
            raise RuntimeError("目标容器正在重启，请等待当前操作完成")
        key = (env, owner, target_key, lock.lock_token)
        preparing = None
        try:
            preparing = asyncio.create_task(asyncio.to_thread(
                strategy.prepare_restart, ctx, binding_id=None,
                device_id=device_id, device_service=self._device_service_provider(),
                bot_repository=None, renew_lease=lambda: self._restart_lock_repo.renew(*key),
                target_runtime=self._baas_service_provider() if self._baas_service_provider else None,
            ))
            # Cancellation must not release the lock while the exec worker runs.
            await asyncio.shield(preparing)
            if self._restart_lock_repo.renew(*key) is not True:
                raise RuntimeError("重启锁已失效，禁止替换旧容器")
            async def keep_submission_lease():
                # The existing stale-reaper TTL is 120s. Provider submission can
                # take longer; keep the same token alive while the caller awaits it.
                while True:
                    await asyncio.sleep(30)
                    if await asyncio.to_thread(self._restart_lock_repo.renew, *key) is not True:
                        raise RuntimeError("重启锁已失效，替换结果需重新确认")

            keeper = asyncio.create_task(keep_submission_lease())
            try:
                yield
                if keeper.done():
                    keeper.result()
            finally:
                keeper.cancel()
                with suppress(asyncio.CancelledError):
                    await keeper
        finally:
            if preparing is not None and not preparing.done():
                # Keep ownership until the detached thread finishes, without
                # turning request cancellation into replacement permission.
                def release_when_done(task):
                    if not task.cancelled():
                        task.exception()
                    self._restart_lock_repo.release(*key)
                preparing.add_done_callback(release_when_done)
            else:
                self._restart_lock_repo.release(*key)
