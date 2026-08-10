"""Repository contracts owned by the ``skills_pool`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset
    from agentclaw.community.core.skills_pool.quarantine import QuarantineRecord
    from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope, BotSkillLayoutState, RolloutEvidence


@runtime_checkable
class SkillsPoolLayoutRepositoryProtocol(Protocol):
    """持久化布局状态，并以 generation/lease 提供 fencing。"""

    @abstractmethod
    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        """读取状态；不存在记录时返回非持久化的 Legacy 缺省状态。"""
        ...

    @abstractmethod
    def list_states(
        self,
        *,
        env: str,
        engine: str | None = None,
        batch_id: str | None = None,
    ) -> list[BotSkillLayoutState]:
        """列出一个环境内已经认领过布局状态的 Bot。"""
        ...

    @abstractmethod
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
        """仅为尚未认领的 Legacy Bot 原子创建一个迁移代际。"""
        ...

    @abstractmethod
    def renew_lease(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> bool:
        """仅允许当前 generation 的未过期持有者续租。"""
        ...

    @abstractmethod
    def try_acquire_lease(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> bool:
        """在 lease 缺失或过期时，以 CAS 竞争成为新持有者。"""
        ...

    @abstractmethod
    def holds_lease(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
    ) -> bool:
        """用数据库时钟确认当前 worker 仍持有未过期 lease。"""
        ...

    @abstractmethod
    def record_ready_probe(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
        evidence: dict[str, object],
    ) -> bool:
        """以 generation/lease CAS 记录当前运行时已具备 Pool 能力。"""
        ...

    @abstractmethod
    def release_not_capable_claim(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        evidence: dict[str, object],
    ) -> bool:
        """记录旧运行时证据，并原子释放尚处于准备阶段的迁移认领。"""
        ...

    @abstractmethod
    def release_changed_engine_claim(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        evidence: dict[str, object],
    ) -> bool:
        """记录引擎身份漂移，并原子释放尚未开始切换的迁移认领。"""
        ...

    @abstractmethod
    def record_cutover_committed(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
        evidence: dict[str, object],
    ) -> bool:
        """记录不可逆的数据面切换已经完成。"""
        ...

    @abstractmethod
    def record_post_cutover_evidence(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
        evidence: dict[str, object],
    ) -> bool:
        """在边界已提交时补齐运行时证据，不重复提交数据面边界。"""
        ...

    @abstractmethod
    def has_quarantine_identity(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
    ) -> bool:
        """确认该 generation 已持久化 quarantine 身份。"""
        ...

    @abstractmethod
    def quarantine_identity_conflicts(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        engine: str,
        path: str,
    ) -> bool:
        """判断运行时身份是否与该 generation 已持久化身份冲突。"""
        ...

    @abstractmethod
    def record_cutover_finalizing(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
        evidence: dict[str, object],
    ) -> bool:
        """记录 bridge 已提交但切换后 local 合并仍需幂等前滚。"""
        ...

    @abstractmethod
    def begin_cutover(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
    ) -> bool:
        """在运行时原子切换前持久化 pre-cutover 阶段。"""
        ...

    @abstractmethod
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
        """持久化尚未跨越数据面边界的结构化失败及审计证据。"""
        ...

    @abstractmethod
    def record_post_cutover_failure(
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
        """持久化不可逆边界后的失败；状态保持为只能前滚。"""
        ...

    @abstractmethod
    def mark_repair_required(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        failure_code: str,
        failure_stage: str,
        evidence: dict[str, object],
    ) -> bool:
        """切换结果无法证明时停止自动收敛并保留证据。"""
        ...

    @abstractmethod
    def resolve_repair(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        operator: str,
        note: str,
        cutover_committed: bool,
    ) -> bool:
        """记录人工核验事实并恢复到安全的前滚阶段。"""
        ...

    @abstractmethod
    def commit_pool_active(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
        local_locators: dict[int, str],
    ) -> bool:
        """在一个事务中更新该 Bot 全部 local locator 并提交 Pool Active。"""
        ...

    @abstractmethod
    def record_runtime_reconciliation(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        observed_at: datetime,
        evidence: dict[str, object],
    ) -> bool:
        """Account for a runtime-ready signal.

        ``True`` means the signal was persisted or is safely obsolete or
        superseded. ``False`` means current state cannot account for it and the
        caller must retry.
        """
        ...

    @abstractmethod
    def record_runtime_reconciliation_failure(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        observed_at: datetime,
        evidence: dict[str, object],
    ) -> bool:
        """Account for a runtime failure, invalidating older READY evidence."""
        ...

    @abstractmethod
    def begin_legacy_rollback(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        operator: str,
        note: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> bool:
        """从 POOL_ACTIVE 原子认领一次显式业务回滚。"""
        ...

    @abstractmethod
    def try_acquire_rollback_lease(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> bool:
        """续租或在旧 lease 过期后接管同一回滚 generation。"""
        ...

    @abstractmethod
    def record_legacy_rollback_committed(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        evidence: dict[str, object],
    ) -> bool:
        """记录 Legacy 已成为数据面权威源。"""
        ...

    @abstractmethod
    def record_rollback_failure(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        failure_code: str,
        failure_stage: str,
        retryable: bool,
        evidence: dict[str, object],
    ) -> bool:
        """持久化显式回滚阶段失败。"""
        ...

    @abstractmethod
    def commit_legacy_active(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        local_locators: dict[int, str],
    ) -> bool:
        """原子恢复 Legacy locator 与布局状态。"""
        ...


class QuarantineRepositoryProtocol(Protocol):
    @abstractmethod
    def get_quarantine(
        self,
        scope: BotSkillLayoutScope,
        migration_generation: str,
    ) -> QuarantineRecord | None: ...

    @abstractmethod
    def mark_cleaned(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        evidence: dict[str, object],
    ) -> bool: ...

    @abstractmethod
    def claim_cleanup(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        lease_seconds: int,
        eligible_before: datetime,
    ) -> bool: ...

    @abstractmethod
    def mark_cleanup_failed(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        evidence: dict[str, object],
    ) -> bool: ...

    @abstractmethod
    def record_cleanup_uncertain(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        evidence: dict[str, object],
    ) -> bool: ...


@runtime_checkable
class SkillsPoolSkillRepositoryProtocol(Protocol):
    """激活所需的 Bot 级 Skill 资产视图。"""

    @abstractmethod
    def list_bot_local_assets(
        self, *, env: str, bot_id: str
    ) -> list[RegisteredSkillAsset]: ...

    @abstractmethod
    def list_bot_active_assets(
        self,
        *,
        env: str,
        bot_id: str,
        user_id: str,
        engine: str,
    ) -> list[RegisteredSkillAsset]: ...


@runtime_checkable
class SkillsPoolRolloutRepositoryProtocol(Protocol):
    @abstractmethod
    def commit_change(
        self,
        *,
        env: str,
        config_id: int | None,
        expected_revision: str | None,
        expected_enable: bool,
        expected_value: dict[str, object],
        next_revision: str,
        enabled: bool,
        value: dict[str, object],
        audit: dict[str, object],
    ) -> bool: ...

    @abstractmethod
    def list_audit_events(self, *, env: str) -> list[dict[str, object]]: ...
