"""Service API Protocol for friend-auth-sync (DI surface)."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class FriendAuthSyncServiceProtocol(Protocol):
    """Sync a human→bot friend relationship to the authorization relationship service (via AuthRelationshipPlugin)."""

    def sync(
        self,
        *,
        bot_id: str,
        owner_work_no: str,
        human_work_no: str,
        action: str,
    ) -> dict[str, Any]:
        """Return ``{"synced": bool, "reason": str, "auth_id": int | None}``.

        Raises ``BotNotFoundError`` / ``AgentCodeUnavailableError`` /
        ``AuthRelationshipSyncError`` (defined in the service module).
        """
        ...
