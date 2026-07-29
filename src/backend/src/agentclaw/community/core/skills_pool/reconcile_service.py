"""已认领 Bot 的 Skills Pool 激活闭环。

本模块只编排控制面步骤；最终同步和原子 bridge 由当前运行时完成，数据库
仓储负责 locator 与 ``POOL_ACTIVE`` 的同事务提交。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import (
    BotRepository,
)
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    CUTOVER_EVIDENCE_CONTRACT_VERSION,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skills_pool.models import (
    FILESYSTEM_POOL_ENGINES,
    PoolCutoverStatus,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    build_logical_skill_mappings,
    local_locators_from_evidence,
    local_skill_name,
)
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.core.skills_pool.ports import (
    SkillsPoolRuntimeProtocol,
    SkillsPoolSkillRepositoryProtocol,
)


class SkillsPoolReconcileOutcome(StrEnum):
    POOL_ACTIVE = "pool_active"
    ALREADY_ACTIVE = "already_active"
    NOT_CLAIMED = "not_claimed"
    LEASE_NOT_HELD = "lease_not_held"
    BOT_NOT_FOUND = "bot_not_found"
    BOT_CHANGED = "bot_changed"
    NOT_CAPABLE = "not_capable"
    TRANSIENT_ERROR = "transient_error"
    INVALID = "invalid"
    STATE_RACE_LOST = "state_race_lost"
    DATA_INCONSISTENT = "data_inconsistent"
    ACTIVE_ENTRY_CONFLICT = "active_entry_conflict"
    CUTOVER_FAILED = "cutover_failed"
    MAPPING_FAILED = "mapping_failed"
    MAPPING_VERIFY_FAILED = "mapping_verify_failed"
    DATABASE_COMMIT_FAILED = "database_commit_failed"
    MANUAL_REPAIR_REQUIRED = "manual_repair_required"


@dataclass(frozen=True, slots=True)
class SkillsPoolReconcileResult:
    outcome: SkillsPoolReconcileOutcome
    preparation_id: str | None = None
    evidence: dict[str, object] | None = None
    retryable: bool | None = None


class SkillsPoolReconcileService:
    """将一个已认领且受支持的多引擎 Bot 前滚到 ``POOL_ACTIVE``。"""

    @inject
    def __init__(
        self,
        *,
        bot_repository: BotRepository,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        skill_repository: SkillsPoolSkillRepositoryProtocol,
        runtime: SkillsPoolRuntimeProtocol,
    ) -> None:
        self._bots = bot_repository
        self._layouts = layout_repository
        self._skills = skill_repository
        self._runtime = runtime

    async def reconcile(
        self,
        *,
        scope: BotSkillLayoutScope,
        lease_owner: str,
    ) -> SkillsPoolReconcileResult:
        state = self._layouts.get(scope)
        if state.phase is SkillLayoutPhase.NEEDS_MANUAL_REPAIR:
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.MANUAL_REPAIR_REQUIRED,
                preparation_id=state.preparation_id,
                evidence=state.last_failure_evidence,
                retryable=False,
            )
        if state.active_layout is SkillLayout.POOL:
            return await self._verify_active_runtime(scope=scope, state=state)
        if (
            not state.persisted
            or state.target_layout is not SkillLayout.POOL
            or state.migration_generation is None
        ):
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.NOT_CLAIMED)
        if state.lease_owner != lease_owner:
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.LEASE_NOT_HELD)

        bot = self._bots.get_by_id_and_entity(scope.bot_id, scope.entity_id)
        if bot is None:
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.BOT_NOT_FOUND)
        engine = bot.get("active_engine")
        if bot.get("env") != scope.env or not isinstance(engine, str):
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.BOT_CHANGED)
        if engine not in FILESYSTEM_POOL_ENGINES:
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.BOT_CHANGED)

        owner_id = bot.get("owner_id")
        if not isinstance(owner_id, (str, int)) or isinstance(owner_id, bool):
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.BOT_CHANGED)
        user_id = str(owner_id)
        generation = state.migration_generation
        if not self._layouts.holds_lease(
            scope=scope,
            migration_generation=generation,
            lease_owner=lease_owner,
        ):
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.LEASE_NOT_HELD)

        probe = await self._runtime.probe(
            bot_id=scope.bot_id,
            user_id=user_id,
            engine=engine,
        )
        if probe.status is not RuntimeLayoutProbeStatus.READY:
            if probe.status is RuntimeLayoutProbeStatus.NOT_CAPABLE:
                released = self._layouts.release_not_capable_claim(
                    scope=scope,
                    migration_generation=generation,
                    lease_owner=lease_owner,
                    evidence=probe.evidence,
                )
                if not released:
                    return SkillsPoolReconcileResult(
                        SkillsPoolReconcileOutcome.STATE_RACE_LOST
                    )
            elif probe.status in {
                RuntimeLayoutProbeStatus.INVALID,
                RuntimeLayoutProbeStatus.TRANSIENT_ERROR,
            }:
                recorded = self._record_failure_for_boundary(
                    scope=scope,
                    generation=generation,
                    lease_owner=lease_owner,
                    cutover_committed=state.data_plane_cutover_committed,
                    failure_code=probe.status.value,
                    failure_stage="runtime_probe",
                    retryable=(
                        probe.status is RuntimeLayoutProbeStatus.TRANSIENT_ERROR
                    ),
                    evidence=probe.evidence,
                )
                if not recorded:
                    return SkillsPoolReconcileResult(
                        SkillsPoolReconcileOutcome.STATE_RACE_LOST
                    )
            return SkillsPoolReconcileResult(
                self._probe_outcome(probe.status),
                evidence=probe.evidence,
                retryable=(probe.status is RuntimeLayoutProbeStatus.TRANSIENT_ERROR),
            )
        if (
            probe.preparation_id is None
            or probe.layout_contract_version != state.layout_contract_version
        ):
            recorded = self._record_failure_for_boundary(
                scope=scope,
                generation=generation,
                lease_owner=lease_owner,
                cutover_committed=state.data_plane_cutover_committed,
                failure_code="INVALID",
                failure_stage="runtime_probe",
                retryable=False,
                evidence=probe.evidence,
            )
            if not recorded:
                return SkillsPoolReconcileResult(
                    SkillsPoolReconcileOutcome.STATE_RACE_LOST
                )
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.INVALID,
                evidence=probe.evidence,
                retryable=False,
            )

        if (
            not state.data_plane_cutover_committed
            and probe.evidence.get("cutover_evidence_contract_version")
            != CUTOVER_EVIDENCE_CONTRACT_VERSION
        ):
            compatibility_evidence = {
                **probe.evidence,
                "reason": "cutover_evidence_contract_not_supported",
                "required_version": CUTOVER_EVIDENCE_CONTRACT_VERSION,
            }
            if state.phase is SkillLayoutPhase.POOL_PREPARING:
                recorded = self._layouts.release_not_capable_claim(
                    scope=scope,
                    migration_generation=generation,
                    lease_owner=lease_owner,
                    evidence=compatibility_evidence,
                )
            else:
                recorded = self._layouts.record_pre_cutover_failure(
                    scope=scope,
                    migration_generation=generation,
                    lease_owner=lease_owner,
                    failure_code="NOT_CAPABLE",
                    failure_stage="runtime_contract",
                    retryable=False,
                    evidence=compatibility_evidence,
                )
            if not recorded:
                return SkillsPoolReconcileResult(
                    SkillsPoolReconcileOutcome.STATE_RACE_LOST
                )
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.NOT_CAPABLE,
                preparation_id=probe.preparation_id,
                evidence=compatibility_evidence,
                retryable=False,
            )

        local_assets = self._skills.list_bot_local_assets(
            env=scope.env,
            bot_id=scope.bot_id,
        )
        active_assets = self._skills.list_bot_active_assets(
            env=scope.env,
            bot_id=scope.bot_id,
            user_id=user_id,
            engine=engine,
        )
        try:
            local_names = [local_skill_name(asset) for asset in local_assets]
            mappings = build_logical_skill_mappings(active_assets)
        except ValueError as error:
            evidence = {"reason": str(error)}
            recorded = self._record_failure_for_boundary(
                scope=scope,
                generation=generation,
                lease_owner=lease_owner,
                cutover_committed=state.data_plane_cutover_committed,
                failure_code="MAPPING_DATA_INVALID",
                failure_stage="mapping_build",
                retryable=False,
                evidence=evidence,
            )
            if not recorded:
                return SkillsPoolReconcileResult(
                    SkillsPoolReconcileOutcome.STATE_RACE_LOST
                )
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.INVALID,
                evidence=evidence,
                retryable=False,
            )

        cutover_finalizing = state.phase is SkillLayoutPhase.POOL_CUTOVER_FINALIZING
        repair_evidence_refresh = state.last_failure_code == "MANUAL_REPAIR_RESOLVED"
        locator_evidence = self._persisted_cutover_evidence(state)
        if (
            not state.data_plane_cutover_committed
            or cutover_finalizing
            or repair_evidence_refresh
        ):
            if not state.data_plane_cutover_committed:
                recorded = self._layouts.record_ready_probe(
                    scope=scope,
                    migration_generation=generation,
                    lease_owner=lease_owner,
                    preparation_id=probe.preparation_id,
                    evidence=probe.evidence,
                )
                if not recorded:
                    return SkillsPoolReconcileResult(
                        SkillsPoolReconcileOutcome.STATE_RACE_LOST
                    )
                if not self._layouts.begin_cutover(
                    scope=scope,
                    migration_generation=generation,
                    lease_owner=lease_owner,
                    preparation_id=probe.preparation_id,
                ):
                    return SkillsPoolReconcileResult(
                        SkillsPoolReconcileOutcome.STATE_RACE_LOST
                    )

            cutover = await self._runtime.cutover(
                bot_id=scope.bot_id,
                user_id=user_id,
                migration_generation=generation,
                preparation_id=probe.preparation_id,
                registered_local_names=local_names,
                mappings=mappings,
            )
            if not cutover.committed:
                evidence = cutover.to_dict()
                if state.data_plane_cutover_committed:
                    outcome, stage, retryable = (
                        self._post_commit_cutover_failure_profile(cutover.status)
                    )
                    recorded = self._layouts.record_post_cutover_failure(
                        scope=scope,
                        migration_generation=generation,
                        lease_owner=lease_owner,
                        failure_code=cutover.status.value,
                        failure_stage=stage,
                        retryable=retryable,
                        evidence=evidence,
                    )
                    if not recorded:
                        return SkillsPoolReconcileResult(
                            SkillsPoolReconcileOutcome.STATE_RACE_LOST,
                            preparation_id=probe.preparation_id,
                        )
                    return SkillsPoolReconcileResult(
                        outcome,
                        preparation_id=probe.preparation_id,
                        evidence=evidence,
                        retryable=retryable,
                    )
                if (
                    cutover.status is PoolCutoverStatus.POST_CUTOVER_SYNC_PENDING
                    or cutover_finalizing
                ):
                    recorded = self._layouts.record_cutover_finalizing(
                        scope=scope,
                        migration_generation=generation,
                        lease_owner=lease_owner,
                        preparation_id=probe.preparation_id,
                        evidence=evidence,
                    )
                    if not recorded:
                        return SkillsPoolReconcileResult(
                            SkillsPoolReconcileOutcome.STATE_RACE_LOST,
                            preparation_id=probe.preparation_id,
                        )
                    return SkillsPoolReconcileResult(
                        SkillsPoolReconcileOutcome.CUTOVER_FAILED,
                        preparation_id=probe.preparation_id,
                        evidence=evidence,
                        retryable=True,
                    )
                if cutover.status is PoolCutoverStatus.UNKNOWN:
                    recorded = self._layouts.mark_repair_required(
                        scope=scope,
                        migration_generation=generation,
                        lease_owner=lease_owner,
                        failure_code=cutover.status.value,
                        failure_stage="cutover_outcome_unknown",
                        evidence=evidence,
                    )
                    if not recorded:
                        return SkillsPoolReconcileResult(
                            SkillsPoolReconcileOutcome.STATE_RACE_LOST,
                            preparation_id=probe.preparation_id,
                        )
                    return SkillsPoolReconcileResult(
                        SkillsPoolReconcileOutcome.MANUAL_REPAIR_REQUIRED,
                        preparation_id=probe.preparation_id,
                        evidence=evidence,
                        retryable=False,
                    )
                failure_profile = self._cutover_failure_profile(cutover.status)
                if failure_profile is not None:
                    outcome, stage, retryable = failure_profile
                    cutover_evidence = cutover.to_dict()
                    recorded = self._layouts.record_pre_cutover_failure(
                        scope=scope,
                        migration_generation=generation,
                        lease_owner=lease_owner,
                        failure_code=cutover.status.value,
                        failure_stage=stage,
                        retryable=retryable,
                        evidence=cutover_evidence,
                    )
                    if not recorded:
                        return SkillsPoolReconcileResult(
                            SkillsPoolReconcileOutcome.STATE_RACE_LOST,
                            preparation_id=probe.preparation_id,
                        )
                    return SkillsPoolReconcileResult(
                        outcome,
                        preparation_id=probe.preparation_id,
                        evidence=cutover_evidence,
                        retryable=retryable,
                    )
                return SkillsPoolReconcileResult(
                    SkillsPoolReconcileOutcome.CUTOVER_FAILED,
                    preparation_id=probe.preparation_id,
                    evidence=cutover.to_dict(),
                )
            if state.data_plane_cutover_committed:
                quarantine_path = cutover.evidence.get("quarantine")
                if (
                    not isinstance(quarantine_path, str) or not quarantine_path
                ) and not self._layouts.has_quarantine_identity(
                    scope=scope,
                    migration_generation=generation,
                ):
                    return self._record_post_cutover_failure(
                        scope=scope,
                        generation=generation,
                        lease_owner=lease_owner,
                        preparation_id=probe.preparation_id,
                        outcome=SkillsPoolReconcileOutcome.TRANSIENT_ERROR,
                        failure_code="RUNTIME_CONTRACT_UPGRADE_REQUIRED",
                        failure_stage="post_cutover_evidence",
                        evidence={
                            **cutover.to_dict(),
                            "reason": (
                                "quarantine_identity_missing_from_runtime_and_db"
                            ),
                            "required_version": (CUTOVER_EVIDENCE_CONTRACT_VERSION),
                        },
                    )
                if not self._layouts.record_post_cutover_evidence(
                    scope=scope,
                    migration_generation=generation,
                    lease_owner=lease_owner,
                    preparation_id=probe.preparation_id,
                    evidence=cutover.to_dict(),
                ):
                    return SkillsPoolReconcileResult(
                        SkillsPoolReconcileOutcome.STATE_RACE_LOST,
                        preparation_id=probe.preparation_id,
                    )
            else:
                if not self._layouts.record_cutover_committed(
                    scope=scope,
                    migration_generation=generation,
                    lease_owner=lease_owner,
                    preparation_id=probe.preparation_id,
                    evidence=cutover.to_dict(),
                ):
                    return SkillsPoolReconcileResult(
                        SkillsPoolReconcileOutcome.STATE_RACE_LOST,
                        preparation_id=probe.preparation_id,
                    )
            locator_evidence = cutover.evidence

        if not self._layouts.holds_lease(
            scope=scope,
            migration_generation=generation,
            lease_owner=lease_owner,
        ):
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.LEASE_NOT_HELD,
                preparation_id=probe.preparation_id,
            )
        if not await self._runtime.publish_mappings(
            bot_id=scope.bot_id,
            user_id=user_id,
            mappings=mappings,
        ):
            return self._record_post_cutover_failure(
                scope=scope,
                generation=generation,
                lease_owner=lease_owner,
                preparation_id=probe.preparation_id,
                outcome=SkillsPoolReconcileOutcome.MAPPING_FAILED,
                failure_code="MAPPING_PUBLISH_FAILED",
                failure_stage="mapping_publish",
                evidence={"mapping_count": len(mappings)},
            )
        if not await self._runtime.verify_mappings(
            bot_id=scope.bot_id,
            user_id=user_id,
            mappings=mappings,
        ):
            return self._record_post_cutover_failure(
                scope=scope,
                generation=generation,
                lease_owner=lease_owner,
                preparation_id=probe.preparation_id,
                outcome=SkillsPoolReconcileOutcome.MAPPING_VERIFY_FAILED,
                failure_code="MAPPING_VERIFY_FAILED",
                failure_stage="mapping_verify",
                evidence={"mapping_count": len(mappings)},
            )

        try:
            local_locators = local_locators_from_evidence(
                local_assets,
                local_names,
                locator_evidence,
            )
        except ValueError as error:
            return self._record_post_cutover_failure(
                scope=scope,
                generation=generation,
                lease_owner=lease_owner,
                preparation_id=probe.preparation_id,
                outcome=SkillsPoolReconcileOutcome.DATABASE_COMMIT_FAILED,
                failure_code="LOCATOR_EVIDENCE_INVALID",
                failure_stage="control_plane_commit",
                evidence={"reason": str(error)},
            )
        if not self._layouts.commit_pool_active(
            scope=scope,
            migration_generation=generation,
            lease_owner=lease_owner,
            preparation_id=probe.preparation_id,
            local_locators=local_locators,
        ):
            return self._record_post_cutover_failure(
                scope=scope,
                generation=generation,
                lease_owner=lease_owner,
                preparation_id=probe.preparation_id,
                outcome=SkillsPoolReconcileOutcome.DATABASE_COMMIT_FAILED,
                failure_code="DATABASE_COMMIT_FAILED",
                failure_stage="control_plane_commit",
                evidence={"local_locator_count": len(local_locators)},
            )
        return SkillsPoolReconcileResult(
            SkillsPoolReconcileOutcome.POOL_ACTIVE,
            preparation_id=probe.preparation_id,
        )

    def _record_post_cutover_failure(
        self,
        *,
        scope: BotSkillLayoutScope,
        generation: str,
        lease_owner: str,
        preparation_id: str,
        outcome: SkillsPoolReconcileOutcome,
        failure_code: str,
        failure_stage: str,
        evidence: dict[str, object],
    ) -> SkillsPoolReconcileResult:
        recorded = self._layouts.record_post_cutover_failure(
            scope=scope,
            migration_generation=generation,
            lease_owner=lease_owner,
            failure_code=failure_code,
            failure_stage=failure_stage,
            retryable=True,
            evidence=evidence,
        )
        if not recorded:
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.STATE_RACE_LOST,
                preparation_id=preparation_id,
            )
        return SkillsPoolReconcileResult(
            outcome,
            preparation_id=preparation_id,
            evidence=evidence,
            retryable=True,
        )

    def _record_failure_for_boundary(
        self,
        *,
        scope: BotSkillLayoutScope,
        generation: str,
        lease_owner: str,
        cutover_committed: bool,
        failure_code: str,
        failure_stage: str,
        retryable: bool,
        evidence: dict[str, object],
    ) -> bool:
        recorder = (
            self._layouts.record_post_cutover_failure
            if cutover_committed
            else self._layouts.record_pre_cutover_failure
        )
        return recorder(
            scope=scope,
            migration_generation=generation,
            lease_owner=lease_owner,
            failure_code=failure_code,
            failure_stage=failure_stage,
            retryable=retryable,
            evidence=evidence,
        )

    async def _verify_active_runtime(
        self,
        *,
        scope: BotSkillLayoutScope,
        state: BotSkillLayoutState,
    ) -> SkillsPoolReconcileResult:
        """Verify the current runtime before accepting post-activation evidence."""

        bot = self._bots.get_by_id_and_entity(scope.bot_id, scope.entity_id)
        if bot is None:
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.BOT_NOT_FOUND)
        engine = bot.get("active_engine")
        if bot.get("env") != scope.env or not isinstance(engine, str):
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.BOT_CHANGED)
        if engine not in FILESYSTEM_POOL_ENGINES:
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.BOT_CHANGED)
        owner_id = bot.get("owner_id")
        if not isinstance(owner_id, (str, int)) or isinstance(owner_id, bool):
            return SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.BOT_CHANGED)

        probe = await self._runtime.probe(
            bot_id=scope.bot_id,
            user_id=str(owner_id),
            engine=engine,
        )
        if probe.status is not RuntimeLayoutProbeStatus.READY:
            return SkillsPoolReconcileResult(
                self._probe_outcome(probe.status),
                evidence=probe.evidence,
                retryable=(probe.status is RuntimeLayoutProbeStatus.TRANSIENT_ERROR),
            )
        if (
            probe.preparation_id is None
            or probe.preparation_id != state.preparation_id
            or probe.layout_contract_version != state.layout_contract_version
        ):
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.INVALID,
                evidence={
                    **(probe.evidence or {}),
                    "reason": "active_runtime_identity_mismatch",
                },
                retryable=False,
            )
        if bot.get("bot_type") == "desktop":
            try:
                mappings = build_logical_skill_mappings(
                    self._skills.list_bot_active_assets(
                        env=scope.env,
                        bot_id=scope.bot_id,
                        user_id=str(owner_id),
                        engine=engine,
                    )
                )
            except ValueError as error:
                return SkillsPoolReconcileResult(
                    SkillsPoolReconcileOutcome.INVALID,
                    evidence={"reason": str(error)},
                    retryable=False,
                )
            if not await self._runtime.publish_mappings(
                bot_id=scope.bot_id,
                user_id=str(owner_id),
                mappings=mappings,
            ):
                return SkillsPoolReconcileResult(
                    SkillsPoolReconcileOutcome.MAPPING_FAILED,
                    evidence={"mapping_count": len(mappings)},
                    retryable=True,
                )
            if not await self._runtime.verify_mappings(
                bot_id=scope.bot_id,
                user_id=str(owner_id),
                mappings=mappings,
            ):
                return SkillsPoolReconcileResult(
                    SkillsPoolReconcileOutcome.MAPPING_VERIFY_FAILED,
                    evidence={"mapping_count": len(mappings)},
                    retryable=True,
                )
        return SkillsPoolReconcileResult(
            SkillsPoolReconcileOutcome.ALREADY_ACTIVE,
            preparation_id=probe.preparation_id,
            evidence=probe.evidence,
        )

    @staticmethod
    def _probe_outcome(
        status: RuntimeLayoutProbeStatus,
    ) -> SkillsPoolReconcileOutcome:
        return {
            RuntimeLayoutProbeStatus.NOT_CAPABLE: (
                SkillsPoolReconcileOutcome.NOT_CAPABLE
            ),
            RuntimeLayoutProbeStatus.TRANSIENT_ERROR: (
                SkillsPoolReconcileOutcome.TRANSIENT_ERROR
            ),
            RuntimeLayoutProbeStatus.INVALID: SkillsPoolReconcileOutcome.INVALID,
        }.get(status, SkillsPoolReconcileOutcome.INVALID)

    @staticmethod
    def _cutover_failure_profile(
        status: PoolCutoverStatus,
    ) -> tuple[SkillsPoolReconcileOutcome, str, bool] | None:
        return {
            PoolCutoverStatus.DATA_INCONSISTENT: (
                SkillsPoolReconcileOutcome.DATA_INCONSISTENT,
                "pre_cutover_validation",
                False,
            ),
            PoolCutoverStatus.ACTIVE_ENTRY_CONFLICT: (
                SkillsPoolReconcileOutcome.ACTIVE_ENTRY_CONFLICT,
                "pre_cutover_validation",
                False,
            ),
            PoolCutoverStatus.NOT_ATOMIC: (
                SkillsPoolReconcileOutcome.CUTOVER_FAILED,
                "atomic_cutover",
                False,
            ),
            PoolCutoverStatus.INVALID: (
                SkillsPoolReconcileOutcome.CUTOVER_FAILED,
                "pre_cutover_validation",
                False,
            ),
            PoolCutoverStatus.TRANSIENT_ERROR: (
                SkillsPoolReconcileOutcome.CUTOVER_FAILED,
                "pre_cutover_filesystem",
                True,
            ),
        }.get(status)

    @classmethod
    def _post_commit_cutover_failure_profile(
        cls,
        status: PoolCutoverStatus,
    ) -> tuple[SkillsPoolReconcileOutcome, str, bool]:
        """Classify refresh failures after the irreversible boundary is known."""

        if status in {
            PoolCutoverStatus.UNKNOWN,
            PoolCutoverStatus.TRANSIENT_ERROR,
            PoolCutoverStatus.POST_CUTOVER_SYNC_PENDING,
        }:
            return (
                SkillsPoolReconcileOutcome.CUTOVER_FAILED,
                "post_cutover_refresh",
                True,
            )
        profile = cls._cutover_failure_profile(status)
        if profile is not None:
            outcome, _, retryable = profile
            return outcome, "post_cutover_refresh", retryable
        return (
            SkillsPoolReconcileOutcome.CUTOVER_FAILED,
            "post_cutover_refresh",
            False,
        )

    @staticmethod
    def _persisted_cutover_evidence(
        state: BotSkillLayoutState,
    ) -> dict[str, object] | None:
        probe_evidence = state.last_probe_evidence
        if not isinstance(probe_evidence, dict):
            return None
        cutover = probe_evidence.get("cutover")
        if not isinstance(cutover, dict):
            return None
        evidence = cutover.get("evidence")
        return evidence if isinstance(evidence, dict) else None


__all__ = [
    "SkillsPoolReconcileOutcome",
    "SkillsPoolReconcileResult",
    "SkillsPoolReconcileService",
]
