"""Service API Protocol for expert-chat session management."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ExpertChatServiceProtocol(Protocol):
    """Service API for expert-chat bot list + session lifecycle."""

    def add_chat_bot(
        self, user_id: str, bot_id: str, owner_id: str
    ) -> Dict[str, Any]: ...

    def list_chat_bots(self, user_id: str) -> List[Dict[str, Any]]: ...

    async def remove_chat_bot(
        self, user_id: str, bot_id: str, owner_id: str
    ) -> bool: ...

    async def get_chat_session(
        self, user_id: str, bot_id: str, owner_id: str, iam_token: Optional[str] = None
    ) -> Dict[str, Any]: ...

    async def delete_chat_session(
        self, user_id: str, bot_id: str, owner_id: str
    ) -> bool: ...
