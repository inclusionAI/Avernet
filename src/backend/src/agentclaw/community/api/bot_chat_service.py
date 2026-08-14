"""Service API Protocol for bot-chat session listing + health."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from agentclaw.community.core.bot_chat.schemas import (
    ConversationDetail,
    HealthCheckData,
    SessionListResponse,
)


@runtime_checkable
class BotChatServiceProtocol(Protocol):
    """Service API for bot-chat conversation/session inspection."""

    async def list_sessions(
        self,
        owner_id: str,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        page: int = 1,
        limit: int = 20,
        bot_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        session_key: str | None = None,
        query: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
        match_mode: str = "exact",
        include_output_match: bool = False,
        time_scope: str = "default",
        log_source: str | None = None,
    ) -> SessionListResponse: ...

    async def get_session(
        self,
        trace_id: str,
        owner_id: str | None = None,
        log_source: str | None = None,
    ) -> ConversationDetail: ...

    async def health_check(self) -> HealthCheckData: ...


@runtime_checkable
class OpenBotChatServiceProtocol(Protocol):
    """Service API for the independently secured Bot Logs OpenAPI."""

    async def list_open_sessions(
        self,
        session_key: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
        page: int = 1,
        limit: int = 100,
    ) -> SessionListResponse: ...

    async def get_open_session(self, trace_id: str) -> ConversationDetail: ...

    async def list_open_user_bot_traces(
        self,
        user_id: str,
        bot_id: str,
        page: int = 1,
        limit: int = 100,
    ) -> SessionListResponse: ...
