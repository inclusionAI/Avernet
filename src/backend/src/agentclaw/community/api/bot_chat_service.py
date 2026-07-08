"""Service API Protocol for bot-chat session listing + health."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.bot_chat.schemas import ConversationDetail, HealthCheckData


@runtime_checkable
class BotChatServiceProtocol(Protocol):
    """Service API for bot-chat conversation/session inspection."""

    async def list_sessions(
        self,
        owner_id: str,
        from_date: Any | None = None,
        to_date: Any | None = None,
        page: int = 1,
        limit: int = 20,
        bot_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        session_key: str | None = None,
        query: str | None = None,
        log_source: str | None = None,
    ) -> Any: ...

    async def get_session(
        self,
        trace_id: str,
        owner_id: str | None = None,
        log_source: str | None = None,
    ) -> ConversationDetail: ...

    async def health_check(self) -> HealthCheckData: ...
