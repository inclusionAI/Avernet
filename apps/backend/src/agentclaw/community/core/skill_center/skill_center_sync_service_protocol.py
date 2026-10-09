"""Service API Protocol for exact-version SC Public synchronization."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.skill_center_sync_contract import (
    SkillCenterSyncSummary,
)


@runtime_checkable
class SkillCenterSyncServiceProtocol(Protocol):
    def sync(self) -> SkillCenterSyncSummary: ...

    async def sync_bootstrap(self) -> SkillCenterSyncSummary: ...


__all__ = ["SkillCenterSyncServiceProtocol"]
