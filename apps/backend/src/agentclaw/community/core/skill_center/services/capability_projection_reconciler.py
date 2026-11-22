"""Dispatch committed capability state through its current Runtime binding."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import partial

from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.capability_projection_types import CapabilityProjectionState
from agentclaw.community.core.repository.protocols.capability_projection import (
    CapabilityProjectionRepositoryProtocol,
)
from agentclaw.community.core.skill_center.capability_projection_intent import (
    capability_projection_intent,
    defer_capability_projection,
)
from agentclaw.community.core.skill_center.capability_projection_reconcile import (
    CapabilityWriteScope,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
    ProjectionScope,
    RuntimeProjectionResult,
)
from agentclaw.community.core.skills_pool.mapping_intent import retired_logical_skill_mappings
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.log import get_logger

from .capability_delivery_guard import (
    CapabilityDeliveryGuard,
    CapabilityDeliveryLease,
    CapabilityDeliveryUnavailable,
)


logger = get_logger()


def _projection_pending(code: str, reason: str) -> RuntimeProjectionResult:
    return RuntimeProjectionResult.pending(code=code, reason=reason)


def _unknown_delivery() -> RuntimeProjectionResult:
    return _projection_pending(
        "CAPABILITY_DELIVERY_OUTCOME_UNKNOWN",
        "The last request may still be running. Synchronization will wait for a new Runtime binding.",
    )


def _covers(current: ProjectionScope, requested: ProjectionScope) -> bool:
    return (
        (current.skills or not requested.skills)
        and (current.mcp or not requested.mcp)
        and (current.claim_all_mcp or not requested.claim_all_mcp)
        and (current.claim_all_mcp or requested.claimed_mcp <= current.claimed_mcp)
        and (current.claim_all_mcp or requested.updated_mcp <= current.updated_mcp)
        and requested.released_mcp <= current.released_mcp
    )


class CapabilityProjectionReconciler:
    def __init__(
        self,
        *,
        repository: CapabilityProjectionRepositoryProtocol,
        projector: BotRuntimeProjectorProtocol,
        guard: CapabilityDeliveryGuard,
        bots: BotRepository,
        defer_enabled: bool,
    ) -> None:
        self._repository = repository
        self._projector = projector
        self._guard = guard
        self._bots = bots
        self.defer_enabled = defer_enabled

    @asynccontextmanager
    async def deferred_apply(
        self, *, bot_id: str, entity_id: str, owner_id: str,
    ) -> AsyncIterator[CapabilityWriteScope]:
        async with self._guard.hold(bot_id=bot_id, entity_id=entity_id):
            previous = await self._projector.snapshot_skill_mappings(
                bot_id=bot_id, owner_id=owner_id,
            )
            yield partial(
                defer_capability_projection,
                bot_id=bot_id,
                entity_id=entity_id,
                retired_mappings=previous,
            )

    async def finish_apply(
        self, *, bot_id: str, entity_id: str, initialize: bool,
    ) -> tuple[str | None, RuntimeProjectionResult]:
        state = await asyncio.to_thread(
            self._repository.read, bot_id=bot_id, entity_id=entity_id,
        )
        if state is None and initialize:
            state = await asyncio.to_thread(
                self._repository.require,
                bot_id=bot_id,
                entity_id=entity_id,
                scope=ProjectionScope.everything(),
            )
        if state is None:
            return None, RuntimeProjectionResult.skipped(reason="NO_COMMITTED_CAPABILITY_CHANGE")
        revision, result = await self._reconcile(bot_id=bot_id, entity_id=entity_id)
        return revision or state.revision, result

    async def reconcile(self, *, bot_id: str, entity_id: str) -> RuntimeProjectionResult:
        _, result = await self._reconcile(bot_id=bot_id, entity_id=entity_id)
        return result

    def observe(self, *, bot_id: str, entity_id: str, revision: str) -> RuntimeProjectionResult:
        state = self._repository.read(bot_id=bot_id, entity_id=entity_id)
        if state is None or state.revision != revision:
            return RuntimeProjectionResult.skipped(reason="CAPABILITY_PROJECTION_SUPERSEDED")
        return self._observed(state)

    async def project_competing_change(
        self,
        *,
        bot_id: str,
        owner_id: str,
        entity_id: str | None,
        scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping],
        legacy: Callable[[], Awaitable[RuntimeProjectionResult]],
    ) -> RuntimeProjectionResult:
        """Use the same send boundary for standalone mutations and lifecycle."""
        if entity_id is None:
            bot = await asyncio.to_thread(self._bots.get_by_id_and_owner, bot_id, owner_id)
            if bot is None:
                return await legacy()
            entity_id = str(bot.get("entity_id") or owner_id)
        if capability_projection_intent(bot_id=bot_id, entity_id=entity_id) is not None:
            return RuntimeProjectionResult.skipped(reason="DEFERRED_TO_MANIFEST_APPLY")
        known = await asyncio.to_thread(
            self._repository.read, bot_id=bot_id, entity_id=entity_id,
        )
        if known is None and not self.defer_enabled:
            return await legacy()
        if retired_mappings and not scope.skills:
            scope = replace(scope, skills=True)
        try:
            async with self._guard.hold(bot_id=bot_id, entity_id=entity_id) as lease:
                state = await asyncio.to_thread(
                    self._repository.prepare, bot_id=bot_id, entity_id=entity_id,
                )
                if state is None:
                    return await legacy()
                if not _covers(state.scope, scope) or not set(retired_mappings).issubset(
                    state.retired_mappings
                ):
                    state = await asyncio.to_thread(
                        self._repository.require,
                        bot_id=bot_id,
                        entity_id=entity_id,
                        scope=scope,
                        retired_mappings=retired_mappings,
                    )
                return await self._deliver(state, lease)
        except CapabilityDeliveryUnavailable:
            return self._delivery_busy()

    async def _reconcile(
        self, *, bot_id: str, entity_id: str,
    ) -> tuple[str | None, RuntimeProjectionResult]:
        try:
            async with self._guard.hold(bot_id=bot_id, entity_id=entity_id) as lease:
                state = await asyncio.to_thread(
                    self._repository.prepare, bot_id=bot_id, entity_id=entity_id,
                )
                if state is None:
                    return None, RuntimeProjectionResult.skipped(reason="NO_CAPABILITY_PROJECTION")
                return state.revision, await self._deliver(state, lease)
        except CapabilityDeliveryUnavailable:
            return None, self._delivery_busy()

    async def _deliver(
        self, state: CapabilityProjectionState, lease: CapabilityDeliveryLease,
    ) -> RuntimeProjectionResult:
        if state.write_blocked or not (state.scope.skills or state.scope.mcp):
            return self._observed(state)
        try:
            plan = await asyncio.to_thread(
                self._projector.resolve_plan,
                bot_id=state.bot_id,
                owner_id=state.owner_id,
                scope=state.scope,
            )
        except Exception:
            logger.exception("Could not resolve committed capability projection bot_id=%s", state.bot_id)
            return _projection_pending(
                "CAPABILITY_PROJECTION_PLAN_UNAVAILABLE",
                "Committed capability configuration could not be read for synchronization.",
            )
        if not is_bot_ready(plan.bot):
            return _projection_pending("BOT_RUNTIME_NOT_READY", "The Bot Runtime is not ready.")
        retired = retired_logical_skill_mappings(
            list(state.retired_mappings),
            list(plan.projection.skill_mappings),
        )
        await lease.verify()
        if not await asyncio.to_thread(self._repository.begin, state):
            return _projection_pending(
                "CAPABILITY_PROJECTION_CHANGED",
                "Capability configuration or the Runtime binding changed before synchronization.",
            )
        try:
            result = await self._projector.apply_plan(
                plan=plan, retired_mappings=retired, scope=state.scope,
            )
        except Exception:
            logger.exception("Capability delivery did not return a receipt bot_id=%s", state.bot_id)
            result = _unknown_delivery()
            write_blocked = True
        else:
            write_blocked = result.delivery_uncertain
            if write_blocked:
                result = RuntimeProjectionResult.combine(result, _unknown_delivery())
        accepted = await asyncio.to_thread(
            self._repository.complete, state, result, write_blocked=write_blocked,
        )
        if not accepted:
            return _projection_pending(
                "CAPABILITY_PROJECTION_CHANGED",
                "Capability configuration or the Runtime binding changed during synchronization.",
            )
        return await asyncio.to_thread(
            self.observe, bot_id=state.bot_id, entity_id=state.entity_id, revision=state.revision,
        )

    @staticmethod
    def _delivery_busy() -> RuntimeProjectionResult:
        return _projection_pending(
            "CAPABILITY_DELIVERY_BUSY",
            "Capability configuration is awaiting the shared Runtime delivery lease.",
        )

    @staticmethod
    def _observed(state: CapabilityProjectionState) -> RuntimeProjectionResult:
        if state.is_current_binding and state.write_blocked:
            if not any(issue.code == "CAPABILITY_DELIVERY_OUTCOME_UNKNOWN" for issue in state.result.issues):
                return RuntimeProjectionResult.combine(state.result, _unknown_delivery())
        return state.result
