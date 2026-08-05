"""Service API Protocol for explicit Skills Pool rollback."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.recovery_service import (
    SkillsPoolRollbackOutcome,
    SkillsPoolRollbackResult,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


@runtime_checkable
class SkillsPoolRollbackServiceProtocol(Protocol):
    """Operator rollback contract.

    ``SERVICE_BOT_UNSUPPORTED`` is a terminal, non-retryable refusal: Service
    Draft rollback requires a Runtime Pin-aware contract and is intentionally
    outside this generic filesystem rollback API.
    """

    async def rollback(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        operator: str,
        note: str,
    ) -> SkillsPoolRollbackResult: ...


__all__ = [
    "SkillsPoolRollbackOutcome",
    "SkillsPoolRollbackResult",
    "SkillsPoolRollbackServiceProtocol",
]
