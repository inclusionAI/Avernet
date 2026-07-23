"""Bot 技能布局状态的统一 ORM 模型。"""

from __future__ import annotations

import json

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

from agentclaw.community.core.base import Base
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    RolloutEvidence,
    SkillLayout,
    SkillLayoutPhase,
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
