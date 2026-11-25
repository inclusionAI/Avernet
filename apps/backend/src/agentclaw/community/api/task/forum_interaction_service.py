"""Service API contract for BBS interactions."""

from __future__ import annotations

from agentclaw.community.core.task.forum.interaction.service_protocol import (
    ForumAuthorStatsServiceProtocol,
    ForumInteractionServiceProtocol,
)

__all__ = [
    "ForumAuthorStatsServiceProtocol",
    "ForumInteractionServiceProtocol",
]
