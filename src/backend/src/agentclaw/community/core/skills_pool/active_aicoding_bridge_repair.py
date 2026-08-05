"""AICoding 已激活布局的 Engine-owned bridge 修复请求。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    build_logical_skill_mappings,
    local_skill_name,
)
from agentclaw.community.core.skills_pool.models import PoolCutoverStatus
from agentclaw.community.core.skills_pool.ports import (
    SkillsPoolRuntimeProtocol,
    SkillsPoolSkillRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
)


class ActiveAICodingBridgeRepairStatus(StrEnum):
    REPAIRED = "repaired"
    MAPPING_INVALID = "mapping_invalid"
    ENGINE_REJECTED = "engine_rejected"
    PROBE_NOT_READY = "probe_not_ready"
    IDENTITY_MISMATCH = "identity_mismatch"


@dataclass(frozen=True, slots=True)
class ActiveAICodingBridgeRepairResult:
    status: ActiveAICodingBridgeRepairStatus
    evidence: dict[str, object]
    preparation_id: str | None
    retryable: bool = False
    cutover_status: PoolCutoverStatus | None = None
    probe_status: RuntimeLayoutProbeStatus | None = None


async def request_active_aicoding_bridge_repair(
    *,
    skills: SkillsPoolSkillRepositoryProtocol,
    runtime: SkillsPoolRuntimeProtocol,
    scope: BotSkillLayoutScope,
    state: BotSkillLayoutState,
    bot_id: str,
    user_id: str,
    engine: str,
    initial_probe: RuntimeLayoutProbeResult,
) -> ActiveAICodingBridgeRepairResult:
    """Ask the Engine to repair only a bridge it can prove is trusted.

    Backend sends current logical mapping intent, but never classifies the
    invalid filesystem entry itself.  The Engine activation operation is the
    authority that either rewrites the historical stable repo bridge or
    rejects every other Legacy/dangling/unknown form.
    """

    generation = state.migration_generation
    if generation is None or state.preparation_id is None:
        return ActiveAICodingBridgeRepairResult(
            ActiveAICodingBridgeRepairStatus.IDENTITY_MISMATCH,
            {
                "reason": "active_runtime_identity_mismatch",
                "initial_probe": initial_probe.evidence,
            },
            preparation_id=None,
        )
    try:
        registered_local_names = [
            local_skill_name(asset)
            for asset in skills.list_bot_local_assets(
                env=scope.env,
                bot_id=scope.bot_id,
            )
        ]
        mappings = build_logical_skill_mappings(
            skills.list_bot_active_assets(
                env=scope.env,
                bot_id=scope.bot_id,
                user_id=user_id,
                engine=engine,
            )
        )
    except ValueError as error:
        return ActiveAICodingBridgeRepairResult(
            ActiveAICodingBridgeRepairStatus.MAPPING_INVALID,
            {"reason": str(error)},
            preparation_id=state.preparation_id,
        )

    repair = await runtime.cutover(
        bot_id=bot_id,
        user_id=user_id,
        migration_generation=generation,
        preparation_id=state.preparation_id,
        registered_local_names=registered_local_names,
        mappings=mappings,
    )
    if not repair.committed:
        return ActiveAICodingBridgeRepairResult(
            ActiveAICodingBridgeRepairStatus.ENGINE_REJECTED,
            {
                "initial_probe": initial_probe.evidence,
                "engine_repair": repair.to_dict(),
            },
            preparation_id=state.preparation_id,
            cutover_status=repair.status,
        )

    refreshed_probe = await runtime.probe(
        bot_id=bot_id,
        user_id=user_id,
        engine=engine,
    )
    if refreshed_probe.status is not RuntimeLayoutProbeStatus.READY:
        return ActiveAICodingBridgeRepairResult(
            ActiveAICodingBridgeRepairStatus.PROBE_NOT_READY,
            {
                "reason": "active_aicoding_bridge_reconciliation_incomplete",
                "initial_probe": initial_probe.evidence,
                "post_publish_probe": refreshed_probe.evidence,
            },
            preparation_id=refreshed_probe.preparation_id,
            retryable=(
                refreshed_probe.status is RuntimeLayoutProbeStatus.TRANSIENT_ERROR
            ),
            probe_status=refreshed_probe.status,
        )
    if (
        refreshed_probe.engine != engine
        or refreshed_probe.preparation_id is None
        or refreshed_probe.preparation_id != state.preparation_id
        or refreshed_probe.layout_contract_version != state.layout_contract_version
    ):
        return ActiveAICodingBridgeRepairResult(
            ActiveAICodingBridgeRepairStatus.IDENTITY_MISMATCH,
            {
                "reason": "active_runtime_identity_mismatch",
                "initial_probe": initial_probe.evidence,
                "post_publish_probe": refreshed_probe.evidence,
            },
            preparation_id=refreshed_probe.preparation_id,
        )
    return ActiveAICodingBridgeRepairResult(
        ActiveAICodingBridgeRepairStatus.REPAIRED,
        {
            **refreshed_probe.evidence,
            "active_aicoding_bridge_reconciled": True,
            "reconciled_mapping_count": len(mappings),
            "engine_repair": repair.to_dict(),
        },
        preparation_id=refreshed_probe.preparation_id,
    )


__all__ = [
    "ActiveAICodingBridgeRepairResult",
    "ActiveAICodingBridgeRepairStatus",
    "request_active_aicoding_bridge_repair",
]
