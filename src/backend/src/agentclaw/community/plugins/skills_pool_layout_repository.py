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
