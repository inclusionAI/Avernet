"""Skills Pool Bot 布局状态仓库的业务边界。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    RolloutEvidence,
)


@runtime_checkable
class SkillsPoolLayoutRepositoryProtocol(Protocol):
    """持久化布局状态，并以 generation/lease 提供 fencing。"""

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        """读取状态；不存在记录时返回非持久化的 Legacy 缺省状态。"""
        ...

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

    def holds_lease(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
    ) -> bool:
        """用数据库时钟确认当前 worker 仍持有未过期 lease。"""
        ...

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
