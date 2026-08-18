"""Service API Protocol for dormant Bot activation."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotDormantActivateServiceProtocol(Protocol):
    """User-scoped service that reactivates a recycled personal cloud Bot.

    Thin public-facing seam over ``core.bot_dormant.activate_service``; the
    concrete service owns the Passport unfreeze + start_bot background flow.
    The HTTP handler retains the ``bot_type == personal`` + cloud-only guard
    and the owner lookup, and delegates only the reactivation orchestration.
    """

    def activate(
        self, bot_id: str, user_id: str, nick_name: str | None = None
    ) -> dict[str, Any]: ...
