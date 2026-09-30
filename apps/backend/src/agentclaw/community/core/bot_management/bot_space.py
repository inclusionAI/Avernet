"""Domain contracts and result types for Bot ownership-Space assignment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agentclaw.community.core.spaces.models import SpaceMemberRecord, SpaceRecord


class BotSpaceAccessProtocol(Protocol):
    """Narrow Space-membership capability consumed by Bot management."""

    def require_space_member(
        self, *, space_id: int, user_id: str
    ) -> tuple[SpaceRecord, SpaceMemberRecord]: ...


@dataclass(frozen=True)
class BotSpaceAssignmentResult:
    """The persisted Bot-to-Space assignment and its resolved Space."""

    bot: dict[str, Any]
    space: SpaceRecord
    changed: bool
