"""Open exact-query service entry points for bot-chat embeds."""

from datetime import datetime, timedelta, timezone
from typing import Any

from agentclaw.community.core.bot_chat.errors import InvalidBotLogQueryError
from agentclaw.community.core.bot_chat.query_support import QueryScope
from agentclaw.community.core.bot_chat.repository import OpenBotChatRepository
from agentclaw.community.core.bot_chat.schemas import (
    ConversationDetail,
    SessionListResponse,
)

_OPEN_PAGE_SIZE = 100
_USER_BOT_TIME_RANGE_HOURS = 72


class OpenBotChatServiceMixin:
    """Expose narrow OpenAPI reads without product-route authorization."""

    _list_sessions_db: Any
    _get_session_db: Any
    _open_repo: OpenBotChatRepository

    async def list_open_sessions(
        self,
        session_key: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
        page: int = 1,
        limit: int = _OPEN_PAGE_SIZE,
    ) -> SessionListResponse:
        """List traces by one exact public identifier without owner filtering."""
        session_key = (session_key.strip() or None) if session_key is not None else None
        biz_scene = (biz_scene.strip() or None) if biz_scene is not None else None
        biz_task_id = (biz_task_id.strip() or None) if biz_task_id is not None else None
        group_id = (group_id.strip() or None) if group_id is not None else None

        session_mode = session_key is not None
        task_mode = biz_scene is not None or biz_task_id is not None
        group_mode = group_id is not None
        if sum((session_mode, task_mode, group_mode)) != 1:
            raise InvalidBotLogQueryError(
                "provide exactly one of session_key, biz_scene+biz_task_id, or group_id"
            )
        if task_mode and (biz_scene is None or biz_task_id is None):
            raise InvalidBotLogQueryError(
                "biz_scene and biz_task_id must be provided together"
            )

        return await self._list_sessions_db(
            owner_id=None,
            from_date=datetime(1970, 1, 1, tzinfo=timezone.utc),
            to_date=datetime.now(timezone.utc),
            page=max(1, page),
            limit=min(max(1, limit), _OPEN_PAGE_SIZE),
            bot_id=None,
            trace_id=None,
            session_id=None,
            session_key=session_key,
            query=None,
            biz_scene=biz_scene,
            biz_task_id=biz_task_id,
            group_id=group_id,
            match_mode="exact",
            include_output_match=False,
            query_scope=QueryScope.OPEN,
        )

    async def get_open_session(self, trace_id: str) -> ConversationDetail:
        """Get an exact trace detail without applying owner access filters."""
        trace_id = trace_id.strip()
        if not trace_id:
            raise InvalidBotLogQueryError("trace_id must not be empty")
        return await self._get_session_db(trace_id, owner_id=None)

    async def list_open_user_bot_traces(
        self,
        user_id: str,
        bot_id: str,
        page: int = 1,
        limit: int = _OPEN_PAGE_SIZE,
    ) -> SessionListResponse:
        """List the recent traces for one explicit user-and-Bot pair.

        This is a query boundary, not caller authorization: the open Gateway
        surface currently permits an authenticated caller to name the pair.
        """
        user_id = user_id.strip()
        bot_id = bot_id.strip()
        if not user_id:
            raise InvalidBotLogQueryError("user_id must not be empty")
        if not bot_id:
            raise InvalidBotLogQueryError("bot_id must not be empty")

        now = datetime.now(timezone.utc)
        from_date = now - timedelta(hours=_USER_BOT_TIME_RANGE_HOURS)
        return self._open_repo.list_user_bot_traces(
            user_id=user_id,
            bot_id=bot_id,
            from_ms=int(from_date.timestamp() * 1000),
            to_ms=int(now.timestamp() * 1000),
            page=max(1, page),
            limit=min(max(1, limit), _OPEN_PAGE_SIZE),
        )
