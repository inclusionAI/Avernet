"""Session-link construction for BBS digest confirmations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

from agentclaw.community.core.errors import ValidationError


@runtime_checkable
class FrontendUrlProviderProtocol(Protocol):
    def get(self) -> str:
        """Return the configured frontend origin, or an empty string."""


class DigestSessionLinkBuilder:
    """Build the current Workbench chat route from configured frontend data."""

    def __init__(self, frontend_url_provider: FrontendUrlProviderProtocol) -> None:
        self._frontend_url_provider = frontend_url_provider

    def build(self, *, bot_id: str, owner_user_id: str, session_id: str) -> str:
        base = self._frontend_url_provider.get().strip().rstrip("/")
        if not base:
            raise ValidationError("frontend URL is not configured")
        query = urlencode(
            {
                "bot": f"{bot_id}:{owner_user_id}",
                "session": session_id,
            }
        )
        return f"{base}/workspace/chat?{query}"


__all__ = [
    "DigestSessionLinkBuilder",
    "FrontendUrlProviderProtocol",
]
