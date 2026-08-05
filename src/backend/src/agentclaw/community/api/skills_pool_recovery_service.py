"""Service API Protocol for operator-directed Skills Pool repair."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.recovery_service import (
    ManualRepairResolution,
    SkillsPoolRecoveryResult,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


@runtime_checkable
class SkillsPoolRecoveryServiceProtocol(Protocol):
    def resolve_repair_state(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        operator: str,
        note: str,
        resolution: ManualRepairResolution,
    ) -> SkillsPoolRecoveryResult: ...
