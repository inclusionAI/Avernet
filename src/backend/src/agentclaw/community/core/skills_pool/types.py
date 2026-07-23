"""Skills Pool 布局状态的领域值。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SkillLayout(StrEnum):
    """Bot 当前或目标技能布局。"""

    LEGACY = "legacy"
    POOL = "pool"


class SkillLayoutPhase(StrEnum):
    """Bot 布局迁移的持久化阶段。"""

    LEGACY_ACTIVE = "legacy_active"
    POOL_PREPARING = "pool_preparing"
    POOL_READY = "pool_ready"
    POOL_ACTIVATING_PRE_CUTOVER = "pool_activating_pre_cutover"
    POOL_CUTOVER_FINALIZING = "pool_cutover_finalizing"
    POOL_CUTOVER_COMMITTED = "pool_cutover_committed"
    POOL_ACTIVE = "pool_active"
    LEGACY_ROLLBACK_PREPARING = "legacy_rollback_preparing"
    LEGACY_ROLLBACK_COMMITTED = "legacy_rollback_committed"
    NEEDS_MANUAL_REPAIR = "manual_repair_required"


@dataclass(frozen=True, slots=True)
class BotSkillLayoutScope:
    """一个 Bot 布局状态的唯一持久化范围。"""

    env: str
    entity_id: str
    bot_id: str


@dataclass(frozen=True, slots=True)
class RolloutEvidence:
    """一次迁移认领命中的灰度配置证据。"""

    env: str
    config_id: int
    config_version: str
    batch_id: str | None
    engine_type: str
    decision_reason: str


@dataclass(frozen=True, slots=True)
class BotSkillLayoutState:
    """Repository 对外返回的 Bot 技能布局状态。"""

    scope: BotSkillLayoutScope
    active_layout: SkillLayout
    target_layout: SkillLayout | None
    phase: SkillLayoutPhase
    migration_generation: str | None
    persisted: bool
    layout_contract_version: str | None = None
    preparation_id: str | None = None
    last_probe_result: str | None = None
    last_probe_evidence: dict[str, object] | None = None
    data_plane_cutover_committed: bool = False
    last_failure_code: str | None = None
    last_failure_stage: str | None = None
    last_failure_retryable: bool | None = None
    last_failure_evidence: dict[str, object] | None = None
    last_failure_at: datetime | None = None
    pool_activated_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    rollout_evidence: RolloutEvidence | None = None
    gmt_create: datetime | None = None
    gmt_modified: datetime | None = None

    @classmethod
    def legacy_default(cls, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        """缺少数据库记录时的向前兼容状态。"""

        return cls(
            scope=scope,
            active_layout=SkillLayout.LEGACY,
            target_layout=None,
            phase=SkillLayoutPhase.LEGACY_ACTIVE,
            migration_generation=None,
            persisted=False,
        )
