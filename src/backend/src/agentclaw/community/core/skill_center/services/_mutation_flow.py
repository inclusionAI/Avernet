"""Commit Desired State, then best-effort project the Runtime.

Runtime files and device availability are observed state, not Installation
truth. Product commands therefore keep a successfully committed Desired State
and report a structured projection outcome instead of compensating it back.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
    ProjectionScope,
    RuntimeProjectionResult,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    retired_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.log import get_logger


logger = get_logger()

_RUNTIME_SNAPSHOT_UNAVAILABLE_ACTION = (
    "Bot 当前不可连接或仍在启动。能力集已保存；待 Bot 恢复后，请再次保存能力集完成同步。"
)


def skill_claim_scope(result: DesiredStateMutation) -> ProjectionScope:
    """A Skill mutation that adds the Skill, and with it its MCP dependencies.

    ``mcp`` follows the dependencies rather than being hard-coded: a Skill with
    none leaves the MCP set untouched, so projecting that half would re-declare
    an unchanged allow-list and re-push an unchanged Passport manifest.

    The codes are candidates. The projector intersects them with the set it
    actually resolved, so a dependency that does not survive projection is
    never delivered.
    """
    return ProjectionScope(
        skills=True,
        mcp=bool(result.mcp_codes),
        claimed_mcp=result.mcp_codes,
    )


def skill_release_scope(result: DesiredStateMutation) -> ProjectionScope:
    """The mirror of ``skill_claim_scope`` for a Skill leaving the Bot.

    Also candidates: another Skill or the default policy may still supply the
    same code, and the projector subtracts the projected set before deleting
    any device configuration.
    """
    return ProjectionScope(
        skills=True,
        mcp=bool(result.mcp_codes),
        released_mcp=result.mcp_codes,
    )


def mcp_claim_scope(result: DesiredStateMutation) -> ProjectionScope:
    """Project only the MCP codes the committed mutation actually claimed."""
    return ProjectionScope(
        mcp=bool(result.mcp_codes),
        claimed_mcp=result.mcp_codes,
    )


def mcp_release_scope(result: DesiredStateMutation) -> ProjectionScope:
    """Project only the MCP codes the committed mutation actually released."""
    return ProjectionScope(
        mcp=bool(result.mcp_codes),
        released_mcp=result.mcp_codes,
    )


class MutationProjectionFlow:
    """Apply one Desired State mutation and report Runtime convergence."""

    def __init__(
        self,
        *,
        repository: CapabilityDesiredStateRepositoryProtocol,
        runtime: BotRuntimeProjectorProtocol,
    ) -> None:
        self._repository = repository
        self._runtime = runtime

    async def apply(
        self,
        *,
        bot: dict,
        bot_id: str,
        engine_type: str | None,
        mutation: Callable[[], DesiredStateMutation],
        runtime_required: bool = True,
        scope: ProjectionScope | None = None,
        scope_from_result: (
            Callable[[DesiredStateMutation], ProjectionScope] | None
        ) = None,
        skip_projection_when_unchanged: bool = False,
    ) -> dict:
        """Run the command; return ``{**item, "changed": ..., **details}``.

        ``runtime_required=False`` skips projection entirely — an inactive-set
        membership change has no runtime projection to apply, preserving the
        legacy inactive draft contract. ``engine_type`` remains a compatibility
        parameter for callers that previously supplied it; Runtime failure no
        longer restores a prior desired snapshot.

        ``scope`` is what this mutation changed, declared by the command that
        knows it. Exactly one of ``scope`` / ``scope_from_result`` must be
        given: there is no "forgot to say" default, because the fallback would
        be a full reconcile — the expensive answer, and never the one a
        mutation wants. A caller with genuinely nothing to narrow passes
        ``ProjectionScope.everything()`` and says so out loud.

        ``scope_from_result`` covers the commands that cannot name their scope
        up front: activate and membership commands learn which MCPs they
        claimed or released only from the mutation result, which the repository
        fills in under the row lock it already holds. Building the scope from a
        second, unlocked query instead could disagree with what was actually
        installed.
        """
        if (scope is None) == (scope_from_result is None):
            raise ValueError("exactly one of scope / scope_from_result is required")
        if not runtime_required:
            result = mutation()
            return {
                **result.item,
                "changed": result.changed,
                **result.details,
                "runtime_projection": RuntimeProjectionResult.skipped(
                    reason="RUNTIME_NOT_REQUIRED"
                ).to_dict(),
            }
        if not is_bot_ready(bot):
            result = mutation()
            return {
                **result.item,
                "changed": result.changed,
                **result.details,
                "runtime_projection": RuntimeProjectionResult.pending(
                    code="BOT_RUNTIME_NOT_READY",
                    reason="Bot 运行环境尚未就绪，能力状态已保存但尚未同步",
                ).to_dict(),
            }
        owner_id = str(bot["owner_id"])
        snapshot_started_at = time.perf_counter()
        try:
            previous_mappings = await self._runtime.snapshot_skill_mappings(
                bot_id=bot_id,
                owner_id=owner_id,
            )
        except Exception:
            previous_mappings = ()
            previous_snapshot_failed = True
        else:
            previous_snapshot_failed = False
            self._log_timing(
                stage="snapshot_before",
                bot_id=bot_id,
                engine_type=engine_type,
                started_at=snapshot_started_at,
                mapping_count=len(previous_mappings),
            )
        mutation_started_at = time.perf_counter()
        result = mutation()
        self._log_timing(
            stage="desired_state_mutation",
            bot_id=bot_id,
            engine_type=engine_type,
            started_at=mutation_started_at,
            changed=result.changed,
            mcp_delta_count=len(result.mcp_codes),
        )
        if skip_projection_when_unchanged and not result.changed:
            return {
                **result.item,
                "changed": False,
                **result.details,
                "runtime_projection": RuntimeProjectionResult.skipped(
                    reason="DESIRED_STATE_UNCHANGED"
                ).to_dict(),
            }
        effective_scope = (
            scope_from_result(result) if scope_from_result is not None else scope
        )
        assert effective_scope is not None  # guaranteed by the check above
        projection_started_at = time.perf_counter()
        runtime_result = await self._project_best_effort(
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            previous_mappings=previous_mappings,
            scope=effective_scope,
            previous_snapshot_failed=previous_snapshot_failed,
        )
        self._log_timing(
            stage="runtime_projection",
            bot_id=bot_id,
            engine_type=engine_type,
            started_at=projection_started_at,
            scope=effective_scope,
        )
        return {
            **result.item,
            "changed": result.changed,
            **result.details,
            "runtime_projection": runtime_result.to_dict(),
        }

    async def _project_best_effort(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        previous_mappings: Sequence[PoolSkillMapping],
        scope: ProjectionScope,
        previous_snapshot_failed: bool,
    ) -> RuntimeProjectionResult:
        if previous_snapshot_failed:
            return RuntimeProjectionResult.pending(
                code="RUNTIME_SNAPSHOT_UNAVAILABLE",
                reason="Bot 运行环境当前不可连接，能力状态已保存但尚未同步",
                suggested_action=_RUNTIME_SNAPSHOT_UNAVAILABLE_ACTION,
            )
        try:
            snapshot_started_at = time.perf_counter()
            current_mappings = await self._runtime.snapshot_skill_mappings(
                bot_id=bot_id,
                owner_id=owner_id,
            )
        except Exception:
            return RuntimeProjectionResult.pending(
                code="RUNTIME_SNAPSHOT_UNAVAILABLE",
                reason="Bot 运行环境当前不可连接，能力状态已保存但尚未同步",
                suggested_action=_RUNTIME_SNAPSHOT_UNAVAILABLE_ACTION,
            )
        self._log_timing(
            stage="snapshot_after",
            bot_id=bot_id,
            engine_type=engine_type,
            started_at=snapshot_started_at,
            mapping_count=len(current_mappings),
            scope=scope,
        )
        try:
            projected = await self._runtime.project(
                bot_id=bot_id,
                owner_id=owner_id,
                retired_mappings=retired_logical_skill_mappings(
                    list(previous_mappings),
                    list(current_mappings),
                ),
                scope=scope,
            )
            # Existing in-process fakes and pre-change Runtime adapters return
            # ``None`` for a successful projection. Treat that compatibility
            # shape as the old all-converged success while new adapters return
            # the structured contract.
            if projected is None:
                return RuntimeProjectionResult.converged()
            return projected
        except Exception:
            return RuntimeProjectionResult.pending(
                code="RUNTIME_PROJECTION_UNAVAILABLE",
                reason="Bot 运行环境当前不可连接，能力状态已保存但尚未同步",
            )

    @staticmethod
    def _log_timing(
        *,
        stage: str,
        bot_id: str,
        engine_type: str | None,
        started_at: float,
        mapping_count: int | None = None,
        changed: bool | None = None,
        mcp_delta_count: int | None = None,
        scope: ProjectionScope | None = None,
    ) -> None:
        logger.info(
            "[MutationProjectionFlow] timing stage=%s bot_id=%s engine_type=%s "
            "duration_ms=%s mapping_count=%s changed=%s mcp_delta_count=%s "
            "scope_skills=%s scope_mcp=%s",
            stage,
            bot_id,
            engine_type,
            round((time.perf_counter() - started_at) * 1000),
            mapping_count,
            changed,
            mcp_delta_count,
            scope.skills if scope is not None else None,
            scope.mcp if scope is not None else None,
        )
