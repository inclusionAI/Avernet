"""Fence delayed Skill snapshots before complete filesystem reconciliation."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable

from injector import inject

from agentclaw.community.core.devices.services.device_context import DeviceOfflineError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.capability_state_contract import BotCapabilityStateReaderProtocol
from agentclaw.community.core.skill_center.runtime_projection_contract import ResolvedSkillPlan, RuntimeProjectionResult
from agentclaw.community.core.skill_center.runtime_resolver import RuntimeProjectionResolver
from agentclaw.community.core.workspace.skill_layout import runtime_layout_engine_for_bot
from agentclaw.community.plugin_api.cache import CacheLockInfrastructureError, CachePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class SkillProjectionGuard:
    """Serialize delivery across Backends and reject superseded declarations.

    The 120-second delivery budget is bounded well inside the 600-second lease.
    Busy/unavailable leases and failed reads never authorize filesystem writes.
    """

    @inject
    def __init__(
        self, cache: CachePlugin, reader: BotCapabilityStateReaderProtocol, bots: BotRepository,
    ) -> None:
        self._cache = cache
        self._reader = reader
        self._bots = bots

    async def deliver(
        self, *, plan: ResolvedSkillPlan,
        send: Callable[[], Awaitable[RuntimeProjectionResult]],
    ) -> RuntimeProjectionResult:
        identity = json.dumps([
            get_current_avernet_tenant(), str(plan.bot["env"]), plan.owner_id, plan.bot_id,
        ])
        key = "skill-projection:" + hashlib.sha256(identity.encode()).hexdigest()
        try:
            token = self._cache.acquire_lock_strict(key, ttl=600)
        except CacheLockInfrastructureError:
            return self._pending("SKILL_PROJECTION_LOCK_UNAVAILABLE")
        if token is None:
            return self._pending("SKILL_PROJECTION_BUSY")
        release = True
        try:
            try:
                bot = self._bots.get_by_id_and_owner(plan.bot_id, plan.owner_id)
                if bot is None or any(
                    str(bot.get(field) or "") != str(plan.bot.get(field) or "")
                    for field in ("env", "active_engine", "entity_id", "binding_id")
                ):
                    return self._pending("SKILL_PROJECTION_STALE")
                if runtime_layout_engine_for_bot(bot) != runtime_layout_engine_for_bot(plan.bot):
                    return self._pending("SKILL_PROJECTION_STALE")
                assets = self._reader.active_skill_assets(bot_id=plan.bot_id, owner_id=plan.owner_id)
                current = RuntimeProjectionResolver().resolve_skills(tuple(assets))
            except Exception:
                return self._pending("SKILL_PROJECTION_SNAPSHOT_UNAVAILABLE")
            if set(current.skill_mappings) != set(plan.projection.skill_mappings):
                return self._pending("SKILL_PROJECTION_STALE")
            # Validate the lease again after synchronous DB reads, before I/O.
            try:
                if not self._cache.renew_lock_strict(key, token, ttl=600):
                    return self._pending("SKILL_PROJECTION_LOCK_LOST")
            except CacheLockInfrastructureError:
                return self._pending("SKILL_PROJECTION_LOCK_UNAVAILABLE")
            try:
                result = await asyncio.wait_for(send(), timeout=120)
                if any(issue.code in {
                    "SKILL_MAPPING_RUNTIME_UNAVAILABLE", "SKILL_MAPPING_RESULT_INVALID",
                } for issue in result.issues):
                    release = False  # The transport did not prove remote completion.
                return result
            except TimeoutError:
                # Cancellation does not cancel remote filesystem work. Quarantine
                # the unknown outcome until the bounded lease expires.
                release = False
                return self._pending("SKILL_PROJECTION_TIMEOUT")
            except DeviceOfflineError:
                # Connection resolution confirmed offline before mapping I/O.
                # A reconnect must not wait behind an unknown-outcome lease.
                raise
            except BaseException:
                release = False
                raise
        finally:
            if release:
                self._cache.release_lock(key, token)

    @staticmethod
    def _pending(code: str) -> RuntimeProjectionResult:
        return RuntimeProjectionResult.pending(
            code=code, reason="技能投影尚未同步，请使用最新能力配置重试或重启 Bot",
        )
