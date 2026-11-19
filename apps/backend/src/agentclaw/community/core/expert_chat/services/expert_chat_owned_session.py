"""Owned-session operations shared by Expert Chat and public OpenAPI."""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from agentclaw.community.core.expert_chat.errors import BotNotFoundError, ConnectionError


class ExpertChatOwnedSessionMixin:
    async def _invoke_session_operation(self, bot, connection, user_id, method, path, **kwargs):
        if self._is_independent_caller(bot):
            return await self._invoke_caller_session(bot, connection, user_id, method, path, **kwargs)
        return await self._transport.invoke(connection, method, path, **kwargs)

    async def get_owned_chat_session(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: str,
        iam_token: Optional[str] = None,
        *,
        bcn_friend_authorized: bool = False, actor_user_id: str | None = None,
    ) -> Dict[str, Any]:
        """Read a session authorized by its Caller container or shared-runtime index."""
        result = await self.list_chat_sessions(
            user_id=user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            session_key=session_key,
            limit=1,
            offset=0,
            iam_token=iam_token,
            bcn_friend_authorized=bcn_friend_authorized,
            actor_user_id=actor_user_id,
        )
        if result.get("need_poll"):
            raise ConnectionError("Bot服务正在启动，请稍后重试", error_code="5001")
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
        *,
        bcn_friend_authorized: bool = False, actor_user_id: str | None = None,
    ) -> Dict[str, Any]:
        bot = self._get_authorized_chat_bot(
            user_id, bot_id, owner_id,
            bcn_friend_authorized=bcn_friend_authorized,
        )
        connection, need_poll = await self._prepare_authorized_session_connection(
            bot,
            user_id,
            owner_id,
            session_key,
            iam_token,
            bcn_friend_authorized=bcn_friend_authorized,
            actor_user_id=actor_user_id,
        )
        if need_poll or connection is None:
            raise ConnectionError("Bot服务正在启动，请稍后重试", error_code="5001")
        encoded = quote(session_key, safe="")
        response = await self._invoke_session_operation(
            bot, connection, user_id,
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
        *,
        bcn_friend_authorized: bool = False,
    ) -> Dict[str, Any]:
        bot = self._get_authorized_chat_bot(
            user_id, bot_id, owner_id,
            bcn_friend_authorized=bcn_friend_authorized,
        )
        connection, need_poll = await self._prepare_authorized_session_connection(
            bot,
            user_id,
            owner_id,
            session_key,
            iam_token,
            bcn_friend_authorized=bcn_friend_authorized,
        )
        if need_poll or connection is None:
            raise ConnectionError("Bot服务正在启动，请稍后重试", error_code="5001")
        encoded = quote(session_key, safe="")
        response = await self._invoke_session_operation(
            bot, connection, user_id,
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
        *,
        bcn_friend_authorized: bool = False,
    ) -> bool:
        bot = self._get_authorized_chat_bot(
            user_id, bot_id, owner_id,
            bcn_friend_authorized=bcn_friend_authorized,
        )
        connection, need_poll = await self._prepare_authorized_session_connection(
            bot,
            user_id,
            owner_id,
            session_key,
            iam_token,
            bcn_friend_authorized=bcn_friend_authorized,
        )
        if need_poll or connection is None:
            raise ConnectionError("Bot服务正在启动，请稍后重试", error_code="5001")
        encoded = quote(session_key, safe="")
        await self._invoke_session_operation(
            bot, connection, user_id, "DELETE", f"/api/sessions/{encoded}/messages"
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
        *,
        bcn_friend_authorized: bool = False,
    ) -> bool:
        bot = self._get_authorized_chat_bot(
            user_id, bot_id, owner_id,
            bcn_friend_authorized=bcn_friend_authorized,
        )
        connection, need_poll = await self._prepare_authorized_session_connection(
            bot,
            user_id,
            owner_id,
            session_key,
            iam_token,
            bcn_friend_authorized=bcn_friend_authorized,
        )
        if need_poll or connection is None:
            raise ConnectionError("Bot服务正在启动，请稍后重试", error_code="5001")
        encoded = quote(session_key, safe="")
        await self._invoke_session_operation(
            bot, connection, user_id,
            "PUT" if favorited else "DELETE",
            f"/api/session-favorites/{encoded}",
            params={"user_id": user_id},
        )
        return True
