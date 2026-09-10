"""Projection decorator that wakes one durable Desktop Skill recovery task."""

from __future__ import annotations

from collections.abc import Sequence

from agentclaw.community.core.skill_center.desktop_skill_recovery_protocol import (
    DesktopSkillRecoveryServiceProtocol,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
    ProjectionScope,
    ResolvedSkillPlan,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping


class RecoveringBotRuntimeProjector(BotRuntimeProjectorProtocol):
    """Add the common recovery completion to ordinary projector calls.

    The recovery task is deliberately wired to the undecorated projector, so
    its own ``apply_plan`` attempt returns a Queue outcome instead of enqueueing
    itself again.
    """

    def __init__(
        self,
        *,
        delegate: BotRuntimeProjectorProtocol,
        recovery: DesktopSkillRecoveryServiceProtocol,
    ) -> None:
        self._delegate = delegate
        self._recovery = recovery

    async def snapshot_skill_mappings(
        self, *, bot_id: str, owner_id: str
    ) -> tuple[PoolSkillMapping, ...]:
        return await self._delegate.snapshot_skill_mappings(
            bot_id=bot_id, owner_id=owner_id
        )

    async def project(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult:
        result = await self._delegate.project(
            bot_id=bot_id,
            owner_id=owner_id,
            retired_mappings=retired_mappings,
            scope=scope,
        )
        self._ensure_if_unresolved(
            result=result, scope=scope, bot_id=bot_id, owner_id=owner_id
        )
        return result

    def resolve_plan(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> ResolvedSkillPlan:
        return self._delegate.resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
            retired_mappings=retired_mappings,
            scope=scope,
        )

    async def apply_plan(
        self,
        *,
        plan: ResolvedSkillPlan,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult:
        result = await self._delegate.apply_plan(
            plan=plan,
            retired_mappings=retired_mappings,
            scope=scope,
        )
        self._ensure_if_unresolved(
            result=result,
            scope=scope,
            bot_id=plan.bot_id,
            owner_id=plan.owner_id,
        )
        return result

    async def project_mcp_and_cli(
        self, *, bot_id: str, owner_id: str, scope: ProjectionScope
    ) -> RuntimeProjectionResult:
        return await self._delegate.project_mcp_and_cli(
            bot_id=bot_id, owner_id=owner_id, scope=scope
        )

    def _ensure_if_unresolved(
        self,
        *,
        result: RuntimeProjectionResult,
        scope: ProjectionScope,
        bot_id: str,
        owner_id: str,
    ) -> None:
        if not scope.skills or result.status in {
            RuntimeProjectionStatus.CONVERGED,
            RuntimeProjectionStatus.SKIPPED,
        }:
            return
        self._recovery.ensure(owner_id=owner_id, bot_id=bot_id)


__all__ = ["RecoveringBotRuntimeProjector"]
