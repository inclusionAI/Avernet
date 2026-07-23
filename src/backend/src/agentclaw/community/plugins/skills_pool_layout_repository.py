"""Skills Pool 布局状态的统一 ORM 仓储实现。"""

from __future__ import annotations

import json
from dataclasses import asdict

from injector import inject
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
)
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    RolloutEvidence,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class SkillsPoolLayoutRepository:
    """在所有部署形态中使用同一套 ORM 状态读取语义。"""

    @inject
    def __init__(self, database: DatabasePlugin) -> None:
        self._database = database

    @staticmethod
    def _scope_filter(scope: BotSkillLayoutScope):
        return (
            BotSkillLayoutStateModel.env == scope.env,
            BotSkillLayoutStateModel.entity_id == scope.entity_id,
            BotSkillLayoutStateModel.bot_id == scope.bot_id,
        )

    @staticmethod
    def _now_plus(session, seconds: int) -> ColumnElement:
        if seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if session.bind.dialect.name == "sqlite":
            return func.datetime(func.now(), text(f"'+{seconds} seconds'"))
        return func.date_add(func.now(), text(f"INTERVAL {seconds} SECOND"))

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        with self._database.transactional_orm_session() as session:
            row = (
                session.query(BotSkillLayoutStateModel)
                .filter(*self._scope_filter(scope))
                .first()
            )
            if row is None:
                return BotSkillLayoutState.legacy_default(scope)
            return row.to_state()

    def claim_pool_migration(
        self,
        *,
        scope: BotSkillLayoutScope,
        layout_contract_version: str,
        migration_generation: str,
        rollout_evidence: RolloutEvidence,
        lease_owner: str,
        lease_seconds: int,
    ) -> BotSkillLayoutState | None:
        """从未持久化或未认领的 Legacy 状态原子认领一次迁移。"""

        evidence_json = json.dumps(asdict(rollout_evidence), ensure_ascii=False)
        try:
            with self._database.transactional_orm_session() as session:
                claim_values = {
                    BotSkillLayoutStateModel.target_layout: SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.phase: (
                        SkillLayoutPhase.POOL_PREPARING.value
                    ),
                    BotSkillLayoutStateModel.layout_contract_version: (
                        layout_contract_version
                    ),
                    BotSkillLayoutStateModel.migration_generation: (
                        migration_generation
                    ),
                    BotSkillLayoutStateModel.rollout_evidence: evidence_json,
                    BotSkillLayoutStateModel.lease_owner: lease_owner,
                    BotSkillLayoutStateModel.lease_expires_at: self._now_plus(
                        session, lease_seconds
                    ),
                }
                affected = (
                    session.query(BotSkillLayoutStateModel)
                    .filter(
                        *self._scope_filter(scope),
                        BotSkillLayoutStateModel.active_layout
                        == SkillLayout.LEGACY.value,
                        BotSkillLayoutStateModel.target_layout.is_(None),
                        BotSkillLayoutStateModel.phase
                        == SkillLayoutPhase.LEGACY_ACTIVE.value,
                    )
                    .update(claim_values, synchronize_session=False)
                )
                if affected == 0:
                    session.add(
                        BotSkillLayoutStateModel(
                            env=scope.env,
                            entity_id=scope.entity_id,
                            bot_id=scope.bot_id,
                            active_layout=SkillLayout.LEGACY.value,
                            target_layout=SkillLayout.POOL.value,
                            phase=SkillLayoutPhase.POOL_PREPARING.value,
                            layout_contract_version=layout_contract_version,
                            migration_generation=migration_generation,
                            rollout_evidence=evidence_json,
                            lease_owner=lease_owner,
                            lease_expires_at=self._now_plus(session, lease_seconds),
                        )
                    )
                    session.flush()

                row = (
                    session.query(BotSkillLayoutStateModel)
                    .filter(
                        *self._scope_filter(scope),
                        BotSkillLayoutStateModel.migration_generation
                        == migration_generation,
                    )
                    .one()
                )
                return row.to_state()
        except IntegrityError:
            return None

    def renew_lease(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> bool:
        """仅由仍持有未过期 lease 的同一迁移代际续租。"""

        with self._database.transactional_orm_session() as session:
            affected = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    *self._scope_filter(scope),
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.lease_owner == lease_owner,
                    BotSkillLayoutStateModel.lease_expires_at > func.now(),
                )
                .update(
                    {
                        BotSkillLayoutStateModel.lease_expires_at: (
                            self._now_plus(session, lease_seconds)
                        )
                    },
                    synchronize_session=False,
                )
            )
        return affected == 1

    def try_acquire_lease(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> bool:
        """只在 lease 可接管时赢得当前 generation 的执行权。"""

        with self._database.transactional_orm_session() as session:
            affected = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    *self._scope_filter(scope),
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.target_layout == SkillLayout.POOL.value,
                    or_(
                        BotSkillLayoutStateModel.lease_expires_at.is_(None),
                        BotSkillLayoutStateModel.lease_expires_at <= func.now(),
                    ),
                )
                .update(
                    {
                        BotSkillLayoutStateModel.lease_owner: lease_owner,
                        BotSkillLayoutStateModel.lease_expires_at: (
                            self._now_plus(session, lease_seconds)
                        ),
                    },
                    synchronize_session=False,
                )
            )
        return affected == 1

    def holds_lease(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
    ) -> bool:
        """用数据库时钟检查 generation/lease，阻止过期 worker 写运行时。"""

        with self._database.transactional_orm_session() as session:
            return (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    *self._scope_filter(scope),
                    BotSkillLayoutStateModel.target_layout
                    == SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.lease_owner == lease_owner,
                    BotSkillLayoutStateModel.lease_expires_at > func.now(),
                )
                .first()
                is not None
            )

    def record_ready_probe(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
        evidence: dict[str, object],
    ) -> bool:
        """仅当前 lease holder 可把已认领状态推进为 ``POOL_READY``。"""

        evidence_json = json.dumps(evidence, ensure_ascii=False)
        with self._database.transactional_orm_session() as session:
            affected = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    *self._scope_filter(scope),
                    BotSkillLayoutStateModel.active_layout
                    == SkillLayout.LEGACY.value,
                    BotSkillLayoutStateModel.target_layout
                    == SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.phase.in_(
                        (
                            SkillLayoutPhase.POOL_PREPARING.value,
                            SkillLayoutPhase.POOL_READY.value,
                            SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER.value,
                        )
                    ),
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.lease_owner == lease_owner,
                    BotSkillLayoutStateModel.lease_expires_at > func.now(),
                )
                .update(
                    {
                        BotSkillLayoutStateModel.phase: (
                            SkillLayoutPhase.POOL_READY.value
                        ),
                        BotSkillLayoutStateModel.preparation_id: preparation_id,
                        BotSkillLayoutStateModel.last_probe_result: "READY",
                        BotSkillLayoutStateModel.last_probe_evidence: evidence_json,
                    },
                    synchronize_session=False,
                )
            )
        return affected == 1

    def record_cutover_committed(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
        evidence: dict[str, object],
    ) -> bool:
        """持久化不可逆边界，后续重试只能继续前滚。"""

        evidence_json = json.dumps(evidence, ensure_ascii=False)
        with self._database.transactional_orm_session() as session:
            affected = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    *self._scope_filter(scope),
                    BotSkillLayoutStateModel.active_layout
                    == SkillLayout.LEGACY.value,
                    BotSkillLayoutStateModel.target_layout
                    == SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.phase.in_(
                        (
                            SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER.value,
                            SkillLayoutPhase.POOL_CUTOVER_COMMITTED.value,
                        )
                    ),
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.preparation_id == preparation_id,
                    BotSkillLayoutStateModel.lease_owner == lease_owner,
                    BotSkillLayoutStateModel.lease_expires_at > func.now(),
                )
                .update(
                    {
                        BotSkillLayoutStateModel.phase: (
                            SkillLayoutPhase.POOL_CUTOVER_COMMITTED.value
                        ),
                        BotSkillLayoutStateModel.data_plane_cutover_committed: 1,
                        BotSkillLayoutStateModel.last_probe_evidence: evidence_json,
                    },
                    synchronize_session=False,
                )
            )
        return affected == 1

    def begin_cutover(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
    ) -> bool:
        """以 CAS 标记下一步将进入不可逆的数据面边界。"""

        with self._database.transactional_orm_session() as session:
            affected = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    *self._scope_filter(scope),
                    BotSkillLayoutStateModel.active_layout
                    == SkillLayout.LEGACY.value,
                    BotSkillLayoutStateModel.target_layout
                    == SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.phase
                    == SkillLayoutPhase.POOL_READY.value,
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.preparation_id == preparation_id,
                    BotSkillLayoutStateModel.lease_owner == lease_owner,
                    BotSkillLayoutStateModel.lease_expires_at > func.now(),
                )
                .update(
                    {
                        BotSkillLayoutStateModel.phase: (
                            SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER.value
                        )
                    },
                    synchronize_session=False,
                )
            )
        return affected == 1

    def record_pre_cutover_failure(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        failure_code: str,
        failure_stage: str,
        retryable: bool,
        evidence: dict[str, object],
    ) -> bool:
        """记录结构化阻塞原因，但不跨越或回退数据面切换边界。"""

        evidence_json = json.dumps(evidence, ensure_ascii=False)
        with self._database.transactional_orm_session() as session:
            affected = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    *self._scope_filter(scope),
                    BotSkillLayoutStateModel.active_layout
                    == SkillLayout.LEGACY.value,
                    BotSkillLayoutStateModel.target_layout
                    == SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.phase.in_(
                        (
                            SkillLayoutPhase.POOL_READY.value,
                            SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER.value,
                        )
                    ),
                    BotSkillLayoutStateModel.data_plane_cutover_committed == 0,
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.lease_owner == lease_owner,
                    BotSkillLayoutStateModel.lease_expires_at > func.now(),
                )
                .update(
                    {
                        BotSkillLayoutStateModel.last_failure_code: failure_code,
                        BotSkillLayoutStateModel.last_failure_stage: (
                            failure_stage
                        ),
                        BotSkillLayoutStateModel.last_failure_retryable: int(
                            retryable
                        ),
                        BotSkillLayoutStateModel.last_failure_evidence: (
                            evidence_json
                        ),
                        BotSkillLayoutStateModel.last_failure_at: func.now(),
                    },
                    synchronize_session=False,
                )
            )
        return affected == 1

    def commit_pool_active(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
        local_locators: dict[int, str],
    ) -> bool:
        """原子提交精确 Bot 范围内的全部 local locator 和布局状态。"""

        pool_prefix = (
            "local:///home/admin/.openclaw/workspace/"
            "skills-pool/skills-local/"
        )
        if any(
            not isinstance(skill_id, int)
            or not locator.startswith(pool_prefix)
            for skill_id, locator in local_locators.items()
        ):
            return False

        with self._database.transactional_orm_session() as session:
            local_rows = (
                session.query(Skill)
                .filter(
                    Skill.env == scope.env,
                    Skill.bolt_id == scope.bot_id,
                    Skill.git_path.like("local://%"),
                )
                .with_for_update()
                .all()
            )
            if {row.id for row in local_rows} != set(local_locators):
                return False

            affected = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    *self._scope_filter(scope),
                    BotSkillLayoutStateModel.active_layout
                    == SkillLayout.LEGACY.value,
                    BotSkillLayoutStateModel.target_layout
                    == SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.phase
                    == SkillLayoutPhase.POOL_CUTOVER_COMMITTED.value,
                    BotSkillLayoutStateModel.data_plane_cutover_committed == 1,
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.preparation_id == preparation_id,
                    BotSkillLayoutStateModel.lease_owner == lease_owner,
                    BotSkillLayoutStateModel.lease_expires_at > func.now(),
                )
                .update(
                    {
                        BotSkillLayoutStateModel.active_layout: (
                            SkillLayout.POOL.value
                        ),
                        BotSkillLayoutStateModel.target_layout: None,
                        BotSkillLayoutStateModel.phase: (
                            SkillLayoutPhase.POOL_ACTIVE.value
                        ),
                        BotSkillLayoutStateModel.pool_activated_at: func.now(),
                        BotSkillLayoutStateModel.lease_owner: None,
                        BotSkillLayoutStateModel.lease_expires_at: None,
                        BotSkillLayoutStateModel.last_failure_code: None,
                        BotSkillLayoutStateModel.last_failure_stage: None,
                        BotSkillLayoutStateModel.last_failure_retryable: None,
                        BotSkillLayoutStateModel.last_failure_evidence: None,
                        BotSkillLayoutStateModel.last_failure_at: None,
                    },
                    synchronize_session=False,
                )
            )
            if affected != 1:
                return False
            for row in local_rows:
                row.git_path = local_locators[row.id]
        return True
