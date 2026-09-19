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
    POOL_INITIALIZING = "pool_initializing"
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
class InitialSkillLayoutSelection:
    """Creation-time Pool authority persisted atomically with a new Bot."""

    layout_contract_version: str
    rollout_evidence: RolloutEvidence


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


def runtime_uses_pool_paths(state: BotSkillLayoutState) -> bool:
    """Whether canonical Pool paths already own runtime reads and writes.

    Runtime cutover retires Legacy storage bridges before the Backend CAS can
    persist ``POOL_ACTIVE``. ``begin_cutover`` is the durable fence immediately
    before that remote call; from that point onward, consumers must stop
    dereferencing Legacy locators even while ``active_layout`` is still Legacy.
    """

    return (
        state.active_layout is SkillLayout.POOL
        or state.data_plane_cutover_committed
        or state.phase
        in {
            SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER,
            SkillLayoutPhase.POOL_CUTOVER_FINALIZING,
            SkillLayoutPhase.POOL_CUTOVER_COMMITTED,
        }
    )


def is_pool_native_state(
    state: BotSkillLayoutState,
    *,
    layout_contract_version: str,
    engine_type: str,
) -> bool:
    """Return whether a row has the exact no-migration Pool-native shape."""

    return (
        state.persisted
        and state.active_layout is SkillLayout.POOL
        and state.target_layout is None
        and state.phase
        in {
            SkillLayoutPhase.POOL_INITIALIZING,
            SkillLayoutPhase.POOL_ACTIVE,
        }
        and state.migration_generation is None
        and state.preparation_id is None
        and not state.data_plane_cutover_committed
        and state.layout_contract_version == layout_contract_version
        and state.rollout_evidence is not None
        and state.rollout_evidence.engine_type == engine_type
    )


def is_migrated_pool_active_state(
    state: BotSkillLayoutState,
    *,
    layout_contract_version: str,
) -> bool:
    """Return whether an active Pool row retains completed migration identity."""

    return (
        state.persisted
        and state.active_layout is SkillLayout.POOL
        and state.target_layout is None
        and state.phase is SkillLayoutPhase.POOL_ACTIVE
        and isinstance(state.migration_generation, str)
        and isinstance(state.preparation_id, str)
        and state.data_plane_cutover_committed
        and state.layout_contract_version == layout_contract_version
    )
