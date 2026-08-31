"""Mutate-project-compensate: the one orchestration under every activation
command.

Internal to the command services (the Set service and the direct-activation
service) — not a public layer. Both promise the same synchronous contract:
success means the runtime converged on the new desired state, failure means
desired state was compensated back. This class is that promise, extracted so
the two services cannot drift.
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
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotReadyError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
    ProjectionScope,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    retired_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.log import get_logger


logger = get_logger()


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
    """Apply one desired-state mutation and synchronously project the runtime.

    On projection failure the mutation is compensated: desired state is
    restored from the mutation's snapshot and the runtime is counter-projected
    before ``SkillSetRuntimeReconcileError`` surfaces.
    """

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

        ``runtime_required=False`` skips readiness and projection entirely —
        an inactive-set membership change has no runtime projection to apply,
        preserving the legacy inactive draft contract. ``engine_type`` scopes
        a compensation's restore to the Sets the mutation could have touched.

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
            return {**result.item, "changed": result.changed, **result.details}
        if not is_bot_ready(bot):
            raise LocalSkillNotReadyError()
        owner_id = str(bot["owner_id"])
        snapshot_started_at = time.perf_counter()
        try:
            previous_mappings = await self._runtime.snapshot_skill_mappings(
                bot_id=bot_id,
                owner_id=owner_id,
            )
        except Exception:
            self._log_timing(
                stage="snapshot_before",
                bot_id=bot_id,
                engine_type=engine_type,
                started_at=snapshot_started_at,
                outcome="error",
            )
            raise
        self._log_timing(
            stage="snapshot_before",
            bot_id=bot_id,
            engine_type=engine_type,
            started_at=snapshot_started_at,
            outcome="success",
            mapping_count=len(previous_mappings),
        )

        mutation_started_at = time.perf_counter()
        try:
            result = mutation()
        except Exception:
            self._log_timing(
                stage="mutation_tx",
                bot_id=bot_id,
                engine_type=engine_type,
                started_at=mutation_started_at,
                outcome="error",
            )
            raise
        self._log_timing(
            stage="mutation_tx",
            bot_id=bot_id,
            engine_type=engine_type,
            started_at=mutation_started_at,
            outcome="success",
            changed=result.changed,
            mcp_delta_count=len(result.mcp_codes),
        )
        if skip_projection_when_unchanged and not result.changed:
            return {**result.item, "changed": False, **result.details}
        effective_scope = (
            scope_from_result(result) if scope_from_result is not None else scope
        )
        assert effective_scope is not None  # guaranteed by the check above
        await self._project_or_compensate(
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            mutation=result,
            previous_mappings=previous_mappings,
            scope=effective_scope,
        )
        return {**result.item, "changed": result.changed, **result.details}

    async def _project_or_compensate(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        mutation: DesiredStateMutation,
        previous_mappings: Sequence[PoolSkillMapping],
        scope: ProjectionScope,
    ) -> None:
        current_mappings: Sequence[PoolSkillMapping] = ()
        try:
            snapshot_started_at = time.perf_counter()
            try:
                current_mappings = await self._runtime.snapshot_skill_mappings(
                    bot_id=bot_id,
                    owner_id=owner_id,
                )
            except Exception:
                self._log_timing(
                    stage="snapshot_after",
                    bot_id=bot_id,
                    engine_type=engine_type,
                    started_at=snapshot_started_at,
                    outcome="error",
                    scope=scope,
                )
                raise
            self._log_timing(
                stage="snapshot_after",
                bot_id=bot_id,
                engine_type=engine_type,
                started_at=snapshot_started_at,
                outcome="success",
                mapping_count=len(current_mappings),
                scope=scope,
            )
            await self._runtime.project(
                bot_id=bot_id,
                owner_id=owner_id,
                retired_mappings=retired_logical_skill_mappings(
                    list(previous_mappings),
                    list(current_mappings),
                ),
                scope=scope,
            )
        except Exception as exc:
            self._repository.restore_desired_state(
                bot_id=bot_id,
                owner_id=owner_id,
                state=mutation.previous_state,
                engine_type=engine_type,
            )
            try:
                await self._runtime.project(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    retired_mappings=retired_logical_skill_mappings(
                        list(current_mappings),
                        list(previous_mappings),
                    ),
                    # Same swap as the mappings above: what the forward
                    # projection claimed is what this one releases.
                    scope=scope.inverted(),
                )
            except Exception as restore_error:
                raise SkillSetRuntimeReconcileError() from restore_error
            raise SkillSetRuntimeReconcileError() from exc

    @staticmethod
    def _log_timing(
        *,
        stage: str,
        bot_id: str,
        engine_type: str | None,
        started_at: float,
        outcome: str,
        mapping_count: int | None = None,
        changed: bool | None = None,
        mcp_delta_count: int | None = None,
        scope: ProjectionScope | None = None,
    ) -> None:
        logger.info(
            "[MutationProjectionFlow] timing stage=%s bot_id=%s "
            "engine_type=%s duration_ms=%.3f outcome=%s mapping_count=%s "
            "changed=%s mcp_delta_count=%s scope_skills=%s scope_mcp=%s",
            stage,
            bot_id,
            engine_type,
            (time.perf_counter() - started_at) * 1000,
            outcome,
            mapping_count,
            changed,
            mcp_delta_count,
            scope.skills if scope is not None else None,
            scope.mcp if scope is not None else None,
        )
