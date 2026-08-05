"""Service API Protocol for explicit Skills Pool rollback."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.recovery_service import (
    SkillsPoolRollbackResult,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


@runtime_checkable
class SkillsPoolRollbackServiceProtocol(Protocol):
    async def rollback(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        operator: str,
        note: str,
    ) -> SkillsPoolRollbackResult: ...
