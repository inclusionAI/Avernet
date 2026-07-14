"""Service API Protocol for caller container instance lifecycle."""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class ExpertChatInstanceServiceProtocol(Protocol):
    """Service API for per-caller BaaS container instance management."""

    async def get_caller_connection(
        self, user_id: str, bot_id: str, owner_id: str, force_upgrade: bool = False
    ) -> Dict[str, Any]: ...