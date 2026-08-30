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

    async def list_chat_sessions(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: Optional[str] = None,
        favorite_only: bool = False,
        limit: int = 20,
        offset: int = 0,
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    async def create_chat_session(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    async def connect_chat_session(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: str,
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    async def delete_owned_chat_session(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: str,
    ) -> bool: ...

    async def get_owned_chat_session(
        self, user_id: str, bot_id: str, owner_id: str, session_key: str,
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    async def list_owned_chat_session_messages(
        self, user_id: str, bot_id: str, owner_id: str, session_key: str,
        limit: int, offset: int = 0, iam_token: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    async def update_owned_chat_session(
        self, user_id: str, bot_id: str, owner_id: str, session_key: str,
        fields: Dict[str, Any], iam_token: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    async def clear_owned_chat_session_messages(
        self, user_id: str, bot_id: str, owner_id: str, session_key: str,
        iam_token: Optional[str] = None,
    ) -> bool: ...

    async def set_owned_chat_session_favorite(
        self, user_id: str, bot_id: str, owner_id: str, session_key: str,
        favorited: bool, iam_token: Optional[str] = None,
    ) -> bool: ...
