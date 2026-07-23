"""已认领 Bot 的 Skills Pool 激活闭环。

本模块只编排控制面步骤；最终同步和原子 bridge 由当前运行时完成，数据库
仓储负责 locator 与 ``POOL_ACTIVE`` 的同事务提交。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import (
    BotRepository,
)
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skills_pool.models import (
    OpenClawPoolPaths,
    PoolCutoverStatus,
    PoolSkillMapping,
    RegisteredSkillAsset,
)
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayout,
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


@dataclass(frozen=True, slots=True)
class SkillsPoolReconcileResult:
    outcome: SkillsPoolReconcileOutcome
    preparation_id: str | None = None
    evidence: dict[str, object] | None = None


class SkillsPoolReconcileService:
    """将一个已认领的 OpenClaw Bot 前滚到 ``POOL_ACTIVE``。"""

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
        self._paths = OpenClawPoolPaths()

    async def reconcile(
        self,
        *,
        scope: BotSkillLayoutScope,
        lease_owner: str,
    ) -> SkillsPoolReconcileResult:
        state = self._layouts.get(scope)
        if state.active_layout is SkillLayout.POOL:
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.ALREADY_ACTIVE,
                preparation_id=state.preparation_id,
            )
        if (
            not state.persisted
            or state.target_layout is not SkillLayout.POOL
            or state.migration_generation is None
        ):
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.NOT_CLAIMED
            )
        if state.lease_owner != lease_owner:
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.LEASE_NOT_HELD
            )

        bot = self._bots.get_by_id_and_entity(scope.bot_id, scope.entity_id)
        if bot is None:
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.BOT_NOT_FOUND
            )
        if (
            bot.get("env") != scope.env
            or bot.get("active_engine") != "openclaw"
        ):
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.BOT_CHANGED
            )

        owner_id = bot.get("owner_id")
        if not isinstance(owner_id, (str, int)) or isinstance(owner_id, bool):
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.BOT_CHANGED
            )
        user_id = str(owner_id)
        generation = state.migration_generation
        if not self._layouts.holds_lease(
            scope=scope,
            migration_generation=generation,
            lease_owner=lease_owner,
        ):
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.LEASE_NOT_HELD
            )

        probe = await self._runtime.probe(
            bot_id=scope.bot_id,
            user_id=user_id,
            engine="openclaw",
        )
        if probe.status is not RuntimeLayoutProbeStatus.READY:
            return SkillsPoolReconcileResult(
                self._probe_outcome(probe.status),
                evidence=probe.evidence,
            )
        if (
            probe.preparation_id is None
            or probe.layout_contract_version != state.layout_contract_version
        ):
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.INVALID,
                evidence=probe.evidence,
            )

        local_assets = self._skills.list_bot_local_assets(
            env=scope.env,
            bot_id=scope.bot_id,
        )
        active_assets = self._skills.list_bot_active_assets(
            env=scope.env,
            bot_id=scope.bot_id,
            user_id=user_id,
            engine="openclaw",
        )
        try:
            local_names = [self._local_name(asset) for asset in local_assets]
            mappings = self._build_pool_mappings(active_assets)
        except ValueError as error:
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.INVALID,
                evidence={"reason": str(error)},
            )

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
                failure_profile = self._cutover_failure_profile(
                    cutover.status
                )
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
                    )
                return SkillsPoolReconcileResult(
                    SkillsPoolReconcileOutcome.CUTOVER_FAILED,
                    preparation_id=probe.preparation_id,
                    evidence=cutover.to_dict(),
                )
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
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.MAPPING_FAILED,
                preparation_id=probe.preparation_id,
            )
        if not await self._runtime.verify_mappings(
            bot_id=scope.bot_id,
            user_id=user_id,
            mappings=mappings,
        ):
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.MAPPING_VERIFY_FAILED,
                preparation_id=probe.preparation_id,
            )

        local_locators = {
            asset.skill_id: f"local://{self._paths.pool_local}/{name}"
            for asset, name in zip(local_assets, local_names, strict=True)
        }
        if not self._layouts.commit_pool_active(
            scope=scope,
            migration_generation=generation,
            lease_owner=lease_owner,
            preparation_id=probe.preparation_id,
            local_locators=local_locators,
        ):
            return SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.DATABASE_COMMIT_FAILED,
                preparation_id=probe.preparation_id,
            )
        return SkillsPoolReconcileResult(
            SkillsPoolReconcileOutcome.POOL_ACTIVE,
            preparation_id=probe.preparation_id,
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

    @staticmethod
    def _source_tail(git_path: str, prefix: str) -> PurePosixPath:
        raw = git_path[len(prefix) :]
        path = PurePosixPath(raw)
        if not raw or path.name in {"", ".", ".."}:
            raise ValueError(f"invalid skill locator: {git_path}")
        return path

    def _local_name(self, asset: RegisteredSkillAsset) -> str:
        if not asset.git_path.startswith("local://"):
            raise ValueError(f"skill {asset.skill_id} is not local")
        return self._source_tail(asset.git_path, "local://").name

    def _build_pool_mappings(
        self,
        assets: list[RegisteredSkillAsset],
    ) -> list[PoolSkillMapping]:
        mappings: list[PoolSkillMapping] = []
        targets: dict[str, str] = {}
        for asset in assets:
            if asset.git_path.startswith("local://"):
                relative = PurePosixPath(self._local_name(asset))
                source = PurePosixPath(self._paths.pool_local) / relative
            elif asset.git_path.startswith("git://"):
                relative = self._source_tail(asset.git_path, "git://")
                source = PurePosixPath(self._paths.pool_repo) / relative
            else:
                continue
            target = str(PurePosixPath(self._paths.active) / relative.name)
            if targets.get(target) == str(source):
                continue
            if target in targets:
                raise ValueError(f"duplicate managed target: {target}")
            targets[target] = str(source)
            mappings.append(PoolSkillMapping(source=str(source), target=target))
        return mappings


__all__ = [
    "SkillsPoolReconcileOutcome",
    "SkillsPoolReconcileResult",
    "SkillsPoolReconcileService",
]
