"""Community app-services — neutral no-op code-platform / BotChat.

The open-source build has no git code-platform integration and no Langfuse-backed
bot-chat trace store, so these services return well-defined empty/neutral
responses (never call a corp backend). Corp-free.
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.core.bot_chat.schemas import ConversationDetail, HealthCheckData


class NoopCodePlatformService:
    """No code-platform integration in the community build."""

    def get_private_token(self, cookie: Optional[str] = None) -> Optional[str]:
        return None

    def search_user_projects(self, *args: Any, **kwargs: Any) -> Any:
        return []


class NoopBotChatService:
    """No bot-chat trace store in the community build — empty/neutral results."""

    async def list_sessions(
        self,
        owner_id: str,
        *args: Any,
        resource_owner_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        return []

    async def get_session(
        self,
        trace_id: str,
        owner_id: str | None = None,
        log_source: str | None = None,
    ) -> ConversationDetail:
        # Neutral empty detail (no trace store) — well-defined, not an error.
        return ConversationDetail(id=trace_id, timestamp="")

    async def health_check(self) -> HealthCheckData:
        return HealthCheckData(status="healthy", langfuse_url=None, error=None)
