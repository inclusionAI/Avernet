"""Operator-directed recovery for a fenced Skills Pool migration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import (
    BotRepository,
)
from agentclaw.community.core.skills_pool.models import (
    PoolCutoverStatus,
    PoolPaths,
    PoolSkillMapping,
    RegisteredSkillAsset,
    pool_paths_for_engine,
)
from agentclaw.community.core.skills_pool.ports import (
    SkillsPoolRuntimeProtocol,
    SkillsPoolSkillRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.reconcile_task import (
    SKILLS_POOL_RECONCILE_DEADLINE_SECONDS,
    SKILLS_POOL_RECONCILE_TASK,
    build_skills_pool_reconcile_payload,
)
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayoutPhase,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)


class ManualRepairResolution(StrEnum):
    """The filesystem fact an operator proved on the current runtime."""

    POOL_COMMITTED = "pool_committed"
    LEGACY_NOT_COMMITTED = "legacy_not_committed"


class SkillsPoolRecoveryOutcome(StrEnum):
    RETRIGGERED = "retriggered"
    NOT_FOUND = "not_found"
    NOT_REPAIR_REQUIRED = "not_repair_required"
    STALE_GENERATION = "stale_generation"
    INVALID_REQUEST = "invalid_request"
    STATE_RACE_LOST = "state_race_lost"


@dataclass(frozen=True, slots=True)
class SkillsPoolRecoveryResult:
    outcome: SkillsPoolRecoveryOutcome


class SkillsPoolRecoveryService:
    """Record an operator finding, then durably resume the same generation."""

    @inject
    def __init__(
        self,
        *,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        task_queue_service: TaskQueueService,
    ) -> None:
        self._layouts = layout_repository
        self._queue = task_queue_service

    def resolve_repair_state(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        operator: str,
        note: str,
        resolution: ManualRepairResolution,
    ) -> SkillsPoolRecoveryResult:
        if not operator.strip() or not note.strip():
            return SkillsPoolRecoveryResult(
                SkillsPoolRecoveryOutcome.INVALID_REQUEST
            )
        state = self._layouts.get(scope)
        if not state.persisted:
            return SkillsPoolRecoveryResult(SkillsPoolRecoveryOutcome.NOT_FOUND)
        if state.phase is not SkillLayoutPhase.NEEDS_MANUAL_REPAIR:
            return SkillsPoolRecoveryResult(
                SkillsPoolRecoveryOutcome.NOT_REPAIR_REQUIRED
            )
        if state.migration_generation != migration_generation:
            return SkillsPoolRecoveryResult(
                SkillsPoolRecoveryOutcome.STALE_GENERATION
            )

        committed = resolution is ManualRepairResolution.POOL_COMMITTED
        if not self._layouts.resolve_repair(
            scope=scope,
            migration_generation=migration_generation,
            operator=operator.strip(),
            note=note.strip(),
            cutover_committed=committed,
        ):
            return SkillsPoolRecoveryResult(
                SkillsPoolRecoveryOutcome.STATE_RACE_LOST
            )

        payload = build_skills_pool_reconcile_payload(
            scope=scope,
            source="manual_repair_resolution",
            signal_identity={
                "operator": operator.strip(),
                "resolution": resolution.value,
            },
        )
        self._queue.enqueue(
            SKILLS_POOL_RECONCILE_TASK,
            payload,
            deadline_seconds=SKILLS_POOL_RECONCILE_DEADLINE_SECONDS,
        )
        return SkillsPoolRecoveryResult(SkillsPoolRecoveryOutcome.RETRIGGERED)


class SkillsPoolRollbackOutcome(StrEnum):
    LEGACY_ACTIVE = "legacy_active"
    NOT_FOUND = "not_found"
    NOT_POOL_ACTIVE = "not_pool_active"
    STALE_GENERATION = "stale_generation"
    INVALID_REQUEST = "invalid_request"
    BOT_CHANGED = "bot_changed"
    STATE_RACE_LOST = "state_race_lost"
    ROLLBACK_FAILED = "rollback_failed"
    MAPPING_FAILED = "mapping_failed"
    MAPPING_VERIFY_FAILED = "mapping_verify_failed"
    DATABASE_COMMIT_FAILED = "database_commit_failed"


@dataclass(frozen=True, slots=True)
class SkillsPoolRollbackResult:
    outcome: SkillsPoolRollbackOutcome
    evidence: dict[str, object] | None = None
    retryable: bool | None = None


class SkillsPoolRollbackService:
    """显式地从当前 Pool 内容重建 Legacy，并只向 Legacy 收敛。"""

    _LEASE_SECONDS = 300

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

    async def rollback(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        operator: str,
        note: str,
    ) -> SkillsPoolRollbackResult:
        if not all(
            value.strip()
            for value in (rollback_generation, lease_owner, operator, note)
        ):
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.INVALID_REQUEST
            )

        state = self._layouts.get(scope)
        if not state.persisted:
            return SkillsPoolRollbackResult(SkillsPoolRollbackOutcome.NOT_FOUND)
        if state.phase is SkillLayoutPhase.POOL_ACTIVE:
            if not self._layouts.begin_legacy_rollback(
                scope=scope,
                rollback_generation=rollback_generation,
                operator=operator.strip(),
                note=note.strip(),
                lease_owner=lease_owner,
                lease_seconds=self._LEASE_SECONDS,
            ):
                return SkillsPoolRollbackResult(
                    SkillsPoolRollbackOutcome.STATE_RACE_LOST
                )
            state = self._layouts.get(scope)
        elif state.phase not in {
            SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING,
            SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED,
        }:
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.NOT_POOL_ACTIVE
            )

        if state.migration_generation != rollback_generation:
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.STALE_GENERATION
            )
        if not self._layouts.try_acquire_rollback_lease(
            scope=scope,
            rollback_generation=rollback_generation,
            lease_owner=lease_owner,
            lease_seconds=self._LEASE_SECONDS,
        ):
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.STATE_RACE_LOST
            )

        bot = self._bots.get_by_id_and_entity(scope.bot_id, scope.entity_id)
        if bot is None or bot.get("env") != scope.env:
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.BOT_CHANGED
            )
        engine = bot.get("active_engine")
        owner_id = bot.get("owner_id")
        if (
            not isinstance(engine, str)
            or not isinstance(owner_id, (str, int))
            or isinstance(owner_id, bool)
        ):
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.BOT_CHANGED
            )
        try:
            paths = pool_paths_for_engine(engine)
        except ValueError:
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.BOT_CHANGED
            )
        user_id = str(owner_id)

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
            local_names = [self._local_name(asset) for asset in local_assets]
            mappings = self._build_legacy_mappings(active_assets, paths=paths)
        except ValueError as error:
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.ROLLBACK_FAILED,
                code="ROLLBACK_DATA_INCONSISTENT",
                stage="rollback_validation",
                retryable=False,
                evidence={"reason": str(error)},
            )

        if state.phase is SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING:
            pre_cutover_mapping = await self._publish_and_verify_mappings(
                scope=scope,
                user_id=user_id,
                mappings=mappings,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
            )
            if pre_cutover_mapping is not None:
                return pre_cutover_mapping
            cutover = await self._runtime.rollback_to_legacy(
                bot_id=scope.bot_id,
                user_id=user_id,
                rollback_generation=rollback_generation,
                registered_local_names=local_names,
            )
            if not cutover.committed:
                return self._failure(
                    scope=scope,
                    rollback_generation=rollback_generation,
                    lease_owner=lease_owner,
                    outcome=SkillsPoolRollbackOutcome.ROLLBACK_FAILED,
                    code=f"ROLLBACK_{cutover.status.value}",
                    stage="filesystem_rollback",
                    # Runtime rollback is idempotent: after a lost response the
                    # same generation can prove ALREADY_COMMITTED, so UNKNOWN
                    # is safe to retry rather than guess filesystem truth.
                    retryable=cutover.status
                    in {
                        PoolCutoverStatus.TRANSIENT_ERROR,
                        PoolCutoverStatus.UNKNOWN,
                    },
                    evidence=cutover.to_dict(),
                )
            if not self._layouts.record_legacy_rollback_committed(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                evidence=cutover.to_dict(),
            ):
                return SkillsPoolRollbackResult(
                    SkillsPoolRollbackOutcome.STATE_RACE_LOST
                )

        # Re-publish after the atomic exchange as an idempotent confirmation.
        # A process that resumes from LEGACY_ROLLBACK_COMMITTED starts here.
        post_cutover_mapping = await self._publish_and_verify_mappings(
            scope=scope,
            user_id=user_id,
            mappings=mappings,
            rollback_generation=rollback_generation,
            lease_owner=lease_owner,
        )
        if post_cutover_mapping is not None:
            return post_cutover_mapping

        local_locators = {
            asset.skill_id: f"local://{paths.legacy_local}/{name}"
            for asset, name in zip(local_assets, local_names, strict=True)
        }
        if not self._layouts.commit_legacy_active(
            scope=scope,
            rollback_generation=rollback_generation,
            lease_owner=lease_owner,
            local_locators=local_locators,
        ):
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.DATABASE_COMMIT_FAILED,
                code="ROLLBACK_DATABASE_COMMIT_FAILED",
                stage="control_plane_commit",
                retryable=True,
                evidence={"local_locator_count": len(local_locators)},
            )
        return SkillsPoolRollbackResult(
            SkillsPoolRollbackOutcome.LEGACY_ACTIVE
        )

    async def _publish_and_verify_mappings(
        self,
        *,
        scope: BotSkillLayoutScope,
        user_id: str,
        mappings: list[PoolSkillMapping],
        rollback_generation: str,
        lease_owner: str,
    ) -> SkillsPoolRollbackResult | None:
        if not await self._runtime.publish_mappings(
            bot_id=scope.bot_id,
            user_id=user_id,
            mappings=mappings,
        ):
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.MAPPING_FAILED,
                code="ROLLBACK_MAPPING_PUBLISH_FAILED",
                stage="mapping_publish",
                retryable=True,
                evidence={"mapping_count": len(mappings)},
            )
        if not await self._runtime.verify_mappings(
            bot_id=scope.bot_id,
            user_id=user_id,
            mappings=mappings,
        ):
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.MAPPING_VERIFY_FAILED,
                code="ROLLBACK_MAPPING_VERIFY_FAILED",
                stage="mapping_verify",
                retryable=True,
                evidence={"mapping_count": len(mappings)},
            )
        return None

    def _failure(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        outcome: SkillsPoolRollbackOutcome,
        code: str,
        stage: str,
        retryable: bool,
        evidence: dict[str, object],
    ) -> SkillsPoolRollbackResult:
        if not self._layouts.record_rollback_failure(
            scope=scope,
            rollback_generation=rollback_generation,
            lease_owner=lease_owner,
            failure_code=code,
            failure_stage=stage,
            retryable=retryable,
            evidence=evidence,
        ):
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.STATE_RACE_LOST
            )
        return SkillsPoolRollbackResult(
            outcome,
            evidence=evidence,
            retryable=retryable,
        )

    @staticmethod
    def _source_tail(git_path: str, prefix: str) -> PurePosixPath:
        raw = git_path[len(prefix) :]
        path = PurePosixPath(raw)
        if not raw or path.name in {"", ".", ".."}:
            raise ValueError(f"invalid skill locator: {git_path}")
        return path

    @classmethod
    def _local_name(cls, asset: RegisteredSkillAsset) -> str:
        if not asset.git_path.startswith("local://"):
            raise ValueError(f"skill {asset.skill_id} is not local")
        return cls._source_tail(asset.git_path, "local://").name

    @classmethod
    def _build_legacy_mappings(
        cls,
        assets: list[RegisteredSkillAsset],
        *,
        paths: PoolPaths,
    ) -> list[PoolSkillMapping]:
        mappings: list[PoolSkillMapping] = []
        targets: dict[str, str] = {}
        for asset in assets:
            if asset.git_path.startswith("local://"):
                relative = PurePosixPath(cls._local_name(asset))
                source = PurePosixPath(paths.legacy_local) / relative
            elif asset.git_path.startswith("git://"):
                relative = cls._source_tail(asset.git_path, "git://")
                source = PurePosixPath(paths.legacy_repo) / relative
            else:
                continue
            target = str(PurePosixPath(paths.active) / relative.name)
            if targets.get(target) == str(source):
                continue
            if target in targets:
                raise ValueError(f"duplicate managed target: {target}")
            targets[target] = str(source)
            mappings.append(PoolSkillMapping(source=str(source), target=target))
        return mappings


__all__ = [
    "ManualRepairResolution",
    "SkillsPoolRecoveryOutcome",
    "SkillsPoolRecoveryResult",
    "SkillsPoolRecoveryService",
    "SkillsPoolRollbackOutcome",
    "SkillsPoolRollbackResult",
    "SkillsPoolRollbackService",
]
