"""Service API contract for changing a Bot's owning Space."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.bot_management.bot_space import (
    BotSpaceAssignmentResult,
)


@runtime_checkable
class BotSpaceServiceProtocol(Protocol):
    """Move an owned Bot to a Space the acting user may use."""

    def change_space(
        self, *, bot_id: str, owner_id: str, space_id: int
    ) -> BotSpaceAssignmentResult: ...
