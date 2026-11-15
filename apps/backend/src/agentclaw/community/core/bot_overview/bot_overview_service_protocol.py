"""Service API Protocol for the non-user Bot overview."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import BotOverview


class BotOverviewNotFoundError(LookupError):
    """No live Bot has the requested id."""


@runtime_checkable
class BotOverviewServiceProtocol(Protocol):
    """Assemble the non-user overview of a Bot."""

    async def get_overview(self, bot_id: str) -> BotOverview:
        """Non-user overview of ``bot_id``.

        Raises :class:`BotOverviewNotFoundError` when the Bot does not exist.
        """
        ...


__all__ = ["BotOverviewNotFoundError", "BotOverviewServiceProtocol"]
