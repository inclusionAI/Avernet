"""Skills Pool 领域服务依赖的运行时与资产仓储端口。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    RuntimeLayoutProbeResult,
)
from agentclaw.community.core.skills_pool.models import (
    PoolCutoverResult,
    PoolSkillMapping,
    RegisteredSkillAsset,
)


@runtime_checkable
class SkillsPoolSkillRepositoryProtocol(Protocol):
    """激活所需的 Bot 级 Skill 资产视图。"""

    def list_bot_local_assets(
        self, *, env: str, bot_id: str
    ) -> list[RegisteredSkillAsset]:
        ...

    def list_bot_active_assets(
        self,
        *,
        env: str,
        bot_id: str,
        user_id: str,
        engine: str,
    ) -> list[RegisteredSkillAsset]:
        ...


@runtime_checkable
class SkillsPoolRuntimeProtocol(Protocol):
    """当前运行环境上的探测、切换和 mapping 边界。"""

    async def probe(
        self,
        *,
        bot_id: str,
        user_id: str,
        engine: str,
    ) -> RuntimeLayoutProbeResult:
        ...

    async def cutover(
        self,
        *,
        bot_id: str,
        user_id: str,
        migration_generation: str,
        preparation_id: str,
        registered_local_names: list[str],
        mappings: list[PoolSkillMapping],
    ) -> PoolCutoverResult:
        ...

    async def publish_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
    ) -> bool:
        ...

    async def verify_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
    ) -> bool:
        ...


__all__ = [
    "SkillsPoolRuntimeProtocol",
    "SkillsPoolSkillRepositoryProtocol",
]
