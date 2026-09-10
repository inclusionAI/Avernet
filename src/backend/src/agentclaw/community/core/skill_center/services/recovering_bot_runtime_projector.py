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
from agentclaw.community.log import get_logger


logger = get_logger()
_SKILL_ISSUE_PREFIXES = ("CENTER_CONTENT_", "SKILL_", "SKILLS_POOL_")


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
        try:
            result = await self._delegate.project(
                bot_id=bot_id,
                owner_id=owner_id,
                retired_mappings=retired_mappings,
                scope=scope,
            )
        except Exception:
            self._ensure_after_projection_exception(
                scope=scope, bot_id=bot_id, owner_id=owner_id
            )
            raise
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
        try:
            result = await self._delegate.apply_plan(
                plan=plan,
                retired_mappings=retired_mappings,
                scope=scope,
            )
        except Exception:
            self._ensure_after_projection_exception(
                scope=scope, bot_id=plan.bot_id, owner_id=plan.owner_id
            )
            raise
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
        if not self._skill_projection_is_unresolved(result=result, scope=scope):
            return
        self._recovery.ensure(owner_id=owner_id, bot_id=bot_id)

    @staticmethod
    def _skill_projection_is_unresolved(
        *, result: RuntimeProjectionResult, scope: ProjectionScope
    ) -> bool:
        if not scope.skills:
            return False
        skill_status = result.components.get("skills")
        if skill_status is not None:
            return skill_status not in {
                RuntimeProjectionStatus.CONVERGED,
                RuntimeProjectionStatus.SKIPPED,
            }
        if any(
            issue.resource_type == "SKILL"
            or issue.code.startswith(_SKILL_ISSUE_PREFIXES)
            for issue in result.issues
        ):
            return True
        if scope.mcp:
            return False
        return result.status not in {
            RuntimeProjectionStatus.CONVERGED,
            RuntimeProjectionStatus.SKIPPED,
        }

    def _ensure_after_projection_exception(
        self, *, scope: ProjectionScope, bot_id: str, owner_id: str
    ) -> None:
        if not scope.skills:
            return
        try:
            self._recovery.ensure(owner_id=owner_id, bot_id=bot_id)
        except Exception:
            logger.exception(
                "[RecoveringBotRuntimeProjector] recovery ensure failed after "
                "projection exception owner_id=%s bot_id=%s",
                owner_id,
                bot_id,
            )


__all__ = ["RecoveringBotRuntimeProjector"]
