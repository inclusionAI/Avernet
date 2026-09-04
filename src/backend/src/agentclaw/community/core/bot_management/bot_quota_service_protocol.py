"""Service contract for Space-scoped Bot quota enforcement."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from agentclaw.community.core.bot_management.bot_quota import BotQuotaSnapshot


@runtime_checkable
class BotQuotaServiceProtocol(Protocol):
    def inspect(self, *, owner_id: str, space_id: int | None) -> BotQuotaSnapshot: ...

    def assert_can_add(
        self,
        *,
        owner_id: str,
        space_id: int | None,
    ) -> BotQuotaSnapshot: ...

    def guard_add(
        self,
        *,
        owner_id: str,
        space_id: int | None,
    ) -> AbstractContextManager[BotQuotaSnapshot]: ...

    def set_team_ceiling(self, *, space_id: int, ceiling: int) -> int: ...

    def reset_team_ceiling(self, *, space_id: int) -> int: ...
