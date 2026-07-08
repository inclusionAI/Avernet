"""BotService plugin Protocol — bot metadata operations contract."""

from __future__ import annotations

from typing import Protocol

from ._models import BotBindingData, LogRelationPayload


class BotServicePlugin(Protocol):
    """Plugin protocol for bot metadata operations.

    Currently supports log-relation reporting; future methods for
    fetching/querying bot metadata should be added here.

    Implementations:
    - AiohttpBotServicePlugin: HTTP-based implementation for production.
    - NoopBotServicePlugin: no-op implementation for tests / disabled mode.
    """

    async def report(self, payload: LogRelationPayload) -> None:
        """Report a log-relation record (fire-and-forget).

        Args:
            payload: Log-relation request body.
        """
        ...

    async def get_binding(
        self, bot_id: str, owner_id: str, stage: str
    ) -> BotBindingData:
        """Query bot binding info from the publish API.

        Args:
            bot_id: Bot identifier.
            owner_id: Owner entity identifier (required query param).
            stage: Lifecycle stage, e.g. ``"online"``, ``"verify"``.

        Returns:
            BotBindingData with binding details.

        Raises:
            PaasError: On transport failure, HTTP error, or envelope failure.
        """
        ...

    async def close(self) -> None:
        """Release underlying resources (HTTP sessions, etc.)."""
        ...
