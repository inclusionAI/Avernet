"""Service API Protocol for operator-triggered Skills Pool commands."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.operator_commands import (
    OperatorCommandResult,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


@runtime_checkable
class SkillsPoolOperatorCommandsServiceProtocol(Protocol):
    def wake(
        self,
        *,
        scope: BotSkillLayoutScope,
        operator: str,
        retry_only: bool = False,
    ) -> OperatorCommandResult: ...
