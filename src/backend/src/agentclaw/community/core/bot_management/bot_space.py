"""Domain result types for Bot ownership-Space assignment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.spaces.models import SpaceRecord


@dataclass(frozen=True)
class BotSpaceAssignmentResult:
    """The persisted Bot-to-Space assignment and its resolved Space."""

    bot: dict[str, Any]
    space: SpaceRecord
    changed: bool
