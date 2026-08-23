"""Owned-session operations shared by Expert Chat and public OpenAPI."""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from agentclaw.community.core.expert_chat.errors import BotNotFoundError, ConnectionError


class ExpertChatOwnedSessionMixin:
    def _require_owned_session(
        self, user_id: str, bot_id: str, owner_id: str, session_key: str
    ) -> Dict[str, Any]:
        """Resolve the chat Bot and prove session ownership before I/O."""
        bot = self._get_authorized_chat_bot(user_id, bot_id, owner_id)
        if not self._repo.get_owned_session(user_id, bot_id, owner_id, session_key):
            raise BotNotFoundError("Session不存在或不属于当前用户")
        return bot

    async def get_owned_chat_session(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: str,
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read one Backend-owned expert-chat session from its existing runtime."""
        result = await self.list_chat_sessions(
            user_id=user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            session_key=session_key,
            limit=1,
            offset=0,
            iam_token=iam_token,
        )
        items = result.get("items") or []
        if not items:
            raise BotNotFoundError("Session不存在或不属于当前用户")
        return items[0]

    async def list_owned_chat_session_messages(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: str,
        limit: int,
        offset: int = 0,
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        bot = self._require_owned_session(user_id, bot_id, owner_id, session_key)
        connection, need_poll = await self._prepare_chat_connection(
            bot, user_id, owner_id, iam_token
        )
        if need_poll or connection is None:
            raise ConnectionError("Bot服务正在启动，请稍后重试", error_code="5001")
        encoded = quote(session_key, safe="")
        response = await self._transport.invoke(
            connection,
            "GET",
            f"/api/sessions/{encoded}/messages",
            params={"limit": limit, "offset": offset},
        )
        data = response.get("data")
        return {
            "items": data if isinstance(data, list) else [],
            "total": response.get("total"),
        }

    async def update_owned_chat_session(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: str,
        fields: Dict[str, Any],
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        bot = self._require_owned_session(user_id, bot_id, owner_id, session_key)
        connection, need_poll = await self._prepare_chat_connection(
            bot, user_id, owner_id, iam_token
        )
        if need_poll or connection is None:
            raise ConnectionError("Bot服务正在启动，请稍后重试", error_code="5001")
        encoded = quote(session_key, safe="")
        response = await self._transport.invoke(
            connection,
            "POST",
            f"/api/sessions/{encoded}/update",
            params=fields,
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise BotNotFoundError("Session不存在或不属于当前用户")
        return data

    async def clear_owned_chat_session_messages(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: str,
        iam_token: Optional[str] = None,
    ) -> bool:
        bot = self._require_owned_session(user_id, bot_id, owner_id, session_key)
        connection, need_poll = await self._prepare_chat_connection(
            bot, user_id, owner_id, iam_token
        )
        if need_poll or connection is None:
            raise ConnectionError("Bot服务正在启动，请稍后重试", error_code="5001")
        encoded = quote(session_key, safe="")
        await self._transport.invoke(
            connection, "DELETE", f"/api/sessions/{encoded}/messages"
        )
        return True

    async def set_owned_chat_session_favorite(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: str,
        favorited: bool,
        iam_token: Optional[str] = None,
    ) -> bool:
        bot = self._require_owned_session(user_id, bot_id, owner_id, session_key)
        connection, need_poll = await self._prepare_chat_connection(
            bot, user_id, owner_id, iam_token
        )
        if need_poll or connection is None:
            raise ConnectionError("Bot服务正在启动，请稍后重试", error_code="5001")
        encoded = quote(session_key, safe="")
        await self._transport.invoke(
            connection,
            "PUT" if favorited else "DELETE",
            f"/api/session-favorites/{encoded}",
            params={"user_id": user_id},
        )
        return True
