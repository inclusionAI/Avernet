"""Skills Pool 领域服务依赖的运行时与资产仓储端口。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    RuntimeLayoutProbeResult,
)
from agentclaw.community.core.skills_pool.models import (
    PoolCutoverResult,
    PoolSkillMapping,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.quarantine import (
    RuntimeQuarantineCleanupResult,
)
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolSkillRepositoryProtocol


@runtime_checkable
class SkillsPoolRuntimeProtocol(Protocol):
    """当前运行环境上的探测、切换和 mapping 边界。"""

    async def probe(
        self,
        *,
        bot_id: str,
        user_id: str,
        engine: str,
    ) -> RuntimeLayoutProbeResult: ...

    async def cutover(
        self,
        *,
        bot_id: str,
        user_id: str,
        migration_generation: str,
        preparation_id: str,
        registered_local_names: list[str],
        mappings: list[PoolSkillMapping],
    ) -> PoolCutoverResult: ...

    async def rollback_to_legacy(
        self,
        *,
        bot_id: str,
        user_id: str,
        rollback_generation: str,
        registered_local_names: list[str],
    ) -> PoolCutoverResult: ...

    async def cleanup_quarantine(
        self,
        *,
        bot_id: str,
        user_id: str,
        engine: str,
        migration_generation: str,
    ) -> RuntimeQuarantineCleanupResult: ...

    async def publish_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
        retired_mappings: Sequence[PoolSkillMapping] = (),
        source_layout: SkillMappingSourceLayout = SkillMappingSourceLayout.POOL,
    ) -> bool: ...

    async def verify_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
        retired_mappings: Sequence[PoolSkillMapping] = (),
        source_layout: SkillMappingSourceLayout = SkillMappingSourceLayout.POOL,
    ) -> bool: ...


__all__ = [
    "SkillsPoolRuntimeProtocol",
    "SkillsPoolSkillRepositoryProtocol",
]
