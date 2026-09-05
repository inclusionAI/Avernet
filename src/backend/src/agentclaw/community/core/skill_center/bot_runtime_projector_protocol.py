"""Public Service API for Bot runtime projection reconciliation."""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol as CoreBotRuntimeProjectorProtocol,
    ProjectionScope,
    ResolvedSkillPlan,
    RuntimeProjectionResult,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping


@runtime_checkable
class BotRuntimeProjectorProtocol(CoreBotRuntimeProjectorProtocol, Protocol):
    """Transport-facing contract; Core depends only on its sibling contract."""

    async def snapshot_skill_mappings(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> tuple[PoolSkillMapping, ...]: ...

    async def project(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult: ...

    def resolve_plan(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> ResolvedSkillPlan: ...

    async def apply_plan(
        self,
        *,
        plan: ResolvedSkillPlan,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult: ...

    async def project_mcp_and_cli(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult: ...


__all__ = ["BotRuntimeProjectorProtocol"]
