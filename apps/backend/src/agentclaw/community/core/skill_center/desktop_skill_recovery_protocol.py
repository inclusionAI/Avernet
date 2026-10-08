"""Service API for level-triggered Desktop Skill recovery."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task_queue.types import EnqueueResult


@runtime_checkable
class DesktopSkillRecoveryServiceProtocol(Protocol):
    """Ensure one current Bot-level recovery task, if the Bot is Desktop."""

    def ensure(self, *, owner_id: str, bot_id: str) -> EnqueueResult | None: ...


__all__ = ["DesktopSkillRecoveryServiceProtocol"]
