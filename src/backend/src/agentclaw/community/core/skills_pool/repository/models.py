"""Bot 技能布局状态的统一 ORM 模型。"""

from __future__ import annotations

import json
from datetime import UTC

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from sqlalchemy.dialects import mysql

from agentclaw.community.core.base import Base
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    RolloutEvidence,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.core.skills_pool.quarantine import (
    QuarantineRecord,
    QuarantineStatus,
    RuntimeReconciliationStatus,
)
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger


class BotSkillLayoutStateModel(Base):
    """``ac_bot_skill_layout_state`` 中的一条 Bot 控制面状态。"""

    __tablename__ = "ac_bot_skill_layout_state"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    env = Column(String(20), nullable=False)
    entity_id = Column(String(512), nullable=False)
    bot_id = Column(String(128), nullable=False)
    active_layout = Column(
        String(20),
        nullable=False,
        default=SkillLayout.LEGACY.value,
    )
    target_layout = Column(String(20), nullable=True)
    phase = Column(
        String(64),
        nullable=False,
        default=SkillLayoutPhase.LEGACY_ACTIVE.value,
    )
    migration_generation = Column(String(64), nullable=True)
    layout_contract_version = Column(String(64), nullable=True)
    preparation_id = Column(String(64), nullable=True)
    last_probe_result = Column(String(32), nullable=True)
    last_probe_evidence = Column(Text, nullable=True)
    data_plane_cutover_committed = Column(
        SmallInteger,
        nullable=False,
        default=0,
    )
    last_failure_code = Column(String(64), nullable=True)
    last_failure_stage = Column(String(64), nullable=True)
    last_failure_retryable = Column(SmallInteger, nullable=True)
    last_failure_evidence = Column(Text, nullable=True)
    last_failure_at = Column(DateTime, nullable=True)
    pool_activated_at = Column(DateTime, nullable=True)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    rollout_evidence = Column(Text, nullable=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "env",
            "entity_id",
            "bot_id",
            name="uk_bot_skill_layout_state_scope",
        ),
        Index(
            "idx_bot_skill_layout_state_lease",
            "env",
            "phase",
            "lease_expires_at",
        ),
    )

    def to_state(self) -> BotSkillLayoutState:
        rollout_value = (
            json.loads(self.rollout_evidence) if self.rollout_evidence else None
        )
        return BotSkillLayoutState(
            scope=BotSkillLayoutScope(
                env=self.env,
                entity_id=self.entity_id,
                bot_id=self.bot_id,
            ),
            active_layout=SkillLayout(self.active_layout),
            target_layout=(
                SkillLayout(self.target_layout) if self.target_layout else None
            ),
            phase=SkillLayoutPhase(self.phase),
            migration_generation=self.migration_generation,
            persisted=True,
            layout_contract_version=self.layout_contract_version,
            preparation_id=self.preparation_id,
            last_probe_result=self.last_probe_result,
            last_probe_evidence=(
                json.loads(self.last_probe_evidence)
                if self.last_probe_evidence
                else None
            ),
            data_plane_cutover_committed=bool(self.data_plane_cutover_committed),
            last_failure_code=self.last_failure_code,
            last_failure_stage=self.last_failure_stage,
            last_failure_retryable=(
                bool(self.last_failure_retryable)
                if self.last_failure_retryable is not None
                else None
            ),
            last_failure_evidence=(
                json.loads(self.last_failure_evidence)
                if self.last_failure_evidence
                else None
            ),
            last_failure_at=self.last_failure_at,
            pool_activated_at=self.pool_activated_at,
            lease_owner=self.lease_owner,
            lease_expires_at=self.lease_expires_at,
            rollout_evidence=(
                RolloutEvidence(**rollout_value) if rollout_value else None
            ),
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


class SkillsPoolRolloutAuditModel(Base):
    """Append-only audit event committed with one rollout config revision."""

    __tablename__ = "ac_skills_pool_rollout_audit"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    env = Column(String(20), nullable=False)
    config_id = Column(AutoIncrementBigInteger, nullable=False)
    action = Column(String(128), nullable=False)
    batch_id = Column(String(128), nullable=True)
    operator = Column(String(128), nullable=False)
    reason = Column(String(512), nullable=False)
    based_on_config_version = Column(String(64), nullable=True)
    effective_config_version = Column(String(64), nullable=False)
    evidence = Column(Text, nullable=True)
    effective_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "env",
            "effective_config_version",
            name="uk_skills_pool_rollout_audit_revision",
        ),
        Index(
            "idx_skills_pool_rollout_audit_batch",
            "env",
            "batch_id",
            "id",
        ),
    )


class SkillMigrationQuarantineModel(Base):
    """One immutable Bot/migration-generation quarantine identity."""

    __tablename__ = "ac_skill_migration_quarantine"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    env = Column(String(20), nullable=False)
    entity_id = Column(String(512), nullable=False)
    bot_id = Column(String(128), nullable=False)
    migration_generation = Column(String(64), nullable=False)
    engine = Column(String(64), nullable=False)
    path = Column(String(1024), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default=QuarantineStatus.RETAINED.value,
    )
    source_evidence = Column(Text, nullable=False)
    pool_activated_at = Column(DateTime, nullable=True)
    runtime_reconciled_at = Column(
        DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql"),
        nullable=True,
    )
    runtime_reconciliation_status = Column(String(16), nullable=True)
    runtime_evidence = Column(Text, nullable=True)
    cleaned_at = Column(DateTime, nullable=True)
    cleanup_evidence = Column(Text, nullable=True)
    cleanup_lease_owner = Column(String(128), nullable=True)
    cleanup_lease_expires_at = Column(DateTime, nullable=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "env",
            "entity_id",
            "bot_id",
            "migration_generation",
            name="uk_skill_migration_quarantine_scope_generation",
        ),
        Index(
            "idx_skill_migration_quarantine_cleanup",
            "env",
            "status",
            "pool_activated_at",
        ),
    )

    def to_record(self) -> QuarantineRecord:
        if self.pool_activated_at is None:
            raise ValueError("quarantine activation timestamp is not committed")
        return QuarantineRecord(
            scope=BotSkillLayoutScope(
                env=self.env,
                entity_id=self.entity_id,
                bot_id=self.bot_id,
            ),
            migration_generation=self.migration_generation,
            engine=self.engine,
            path=self.path,
            status=QuarantineStatus(self.status),
            created_at=self.gmt_create.replace(tzinfo=UTC),
            pool_activated_at=self.pool_activated_at.replace(tzinfo=UTC),
            source_evidence=json.loads(self.source_evidence),
            runtime_reconciled_at=(
                self.runtime_reconciled_at.replace(tzinfo=UTC)
                if self.runtime_reconciled_at
                else None
            ),
            runtime_reconciliation_status=(
                RuntimeReconciliationStatus(self.runtime_reconciliation_status)
                if self.runtime_reconciliation_status
                else None
            ),
            runtime_evidence=(
                json.loads(self.runtime_evidence) if self.runtime_evidence else None
            ),
            cleaned_at=(
                self.cleaned_at.replace(tzinfo=UTC) if self.cleaned_at else None
            ),
            cleanup_evidence=(
                json.loads(self.cleanup_evidence) if self.cleanup_evidence else None
            ),
            cleanup_lease_expires_at=(
                self.cleanup_lease_expires_at.replace(tzinfo=UTC)
                if self.cleanup_lease_expires_at
                else None
            ),
        )
