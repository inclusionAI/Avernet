"""Converge post-cutover runtime mappings to current product state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentclaw.community.core.skills_pool.mapping_intent import (
    build_logical_skill_mappings,
    merge_retired_logical_skill_mappings,
    retired_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.core.skills_pool.ports import (
    SkillsPoolRuntimeProtocol,
    SkillsPoolSkillRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


_MAPPING_CONVERGENCE_LIMIT = 4


class MappingConvergenceStatus(StrEnum):
    CONVERGED = "converged"
    LEASE_NOT_HELD = "lease_not_held"
    INVALID = "invalid"
    PUBLISH_FAILED = "publish_failed"
    VERIFY_FAILED = "verify_failed"
    SNAPSHOT_CHANGED = "snapshot_changed"


@dataclass(frozen=True, slots=True)
class MappingConvergenceResult:
    status: MappingConvergenceStatus
    evidence: dict[str, object]


async def converge_post_cutover_mappings(
    *,
    layouts: SkillsPoolLayoutRepositoryProtocol,
    skills: SkillsPoolSkillRepositoryProtocol,
    runtime: SkillsPoolRuntimeProtocol,
    scope: BotSkillLayoutScope,
    generation: str,
    lease_owner: str,
    user_id: str,
    engine: str,
    cutover_mappings: list[PoolSkillMapping],
    durable_retired_mappings: list[PoolSkillMapping],
) -> MappingConvergenceResult:
    """Publish until Engine state matches one stable product-state snapshot."""

    if not layouts.holds_lease(
        scope=scope,
        migration_generation=generation,
        lease_owner=lease_owner,
    ):
        return MappingConvergenceResult(
            MappingConvergenceStatus.LEASE_NOT_HELD,
            {},
        )
    try:
        mappings = build_logical_skill_mappings(
            skills.list_bot_active_assets(
                env=scope.env,
                bot_id=scope.bot_id,
                user_id=user_id,
                engine=engine,
            )
        )
    except ValueError as error:
        return MappingConvergenceResult(
            MappingConvergenceStatus.INVALID,
            {"reason": str(error)},
        )
    retired_mappings = merge_retired_logical_skill_mappings(
        durable_retired_mappings,
        retired_logical_skill_mappings(cutover_mappings, mappings),
        current=mappings,
    )

    for convergence_attempt in range(1, _MAPPING_CONVERGENCE_LIMIT + 1):
        if not layouts.holds_lease(
            scope=scope,
            migration_generation=generation,
            lease_owner=lease_owner,
        ):
            return MappingConvergenceResult(
                MappingConvergenceStatus.LEASE_NOT_HELD,
                {},
            )
        evidence: dict[str, object] = {
            "mapping_count": len(mappings),
            "retired_mappings": [mapping.to_dict() for mapping in retired_mappings],
            "convergence_attempt": convergence_attempt,
        }
        if not await runtime.publish_mappings(
            bot_id=scope.bot_id,
            user_id=user_id,
            mappings=mappings,
            retired_mappings=retired_mappings,
        ):
            return MappingConvergenceResult(
                MappingConvergenceStatus.PUBLISH_FAILED,
                evidence,
            )
        if not await runtime.verify_mappings(
            bot_id=scope.bot_id,
            user_id=user_id,
            mappings=mappings,
            retired_mappings=retired_mappings,
        ):
            return MappingConvergenceResult(
                MappingConvergenceStatus.VERIFY_FAILED,
                evidence,
            )
        try:
            observed_mappings = build_logical_skill_mappings(
                skills.list_bot_active_assets(
                    env=scope.env,
                    bot_id=scope.bot_id,
                    user_id=user_id,
                    engine=engine,
                )
            )
        except ValueError as error:
            return MappingConvergenceResult(
                MappingConvergenceStatus.INVALID,
                {**evidence, "reason": str(error)},
            )
        if set(observed_mappings) == set(mappings):
            return MappingConvergenceResult(
                MappingConvergenceStatus.CONVERGED,
                evidence,
            )
        retired_mappings = merge_retired_logical_skill_mappings(
            retired_mappings,
            retired_logical_skill_mappings(mappings, observed_mappings),
            current=observed_mappings,
        )
        mappings = observed_mappings

    return MappingConvergenceResult(
        MappingConvergenceStatus.SNAPSHOT_CHANGED,
        {
            "mapping_count": len(mappings),
            "retired_mappings": [mapping.to_dict() for mapping in retired_mappings],
            "convergence_attempt": _MAPPING_CONVERGENCE_LIMIT,
        },
    )


__all__ = [
    "MappingConvergenceResult",
    "MappingConvergenceStatus",
    "converge_post_cutover_mappings",
]
