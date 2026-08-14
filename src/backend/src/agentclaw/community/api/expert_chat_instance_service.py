"""Service API Protocol for caller container instance lifecycle."""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class ExpertChatInstanceServiceProtocol(Protocol):
    """Service API for per-caller BaaS container instance management.

    The ``iam_token`` parameter is reserved for future use in caller-container
    IAM-scoped requests. It is currently unused in the implementation, allowing
    callers to pass None or omit it. When IAM integration is enabled, the token
    will be used to authorize container lifecycle operations on behalf of the
    caller.
    """

    async def get_caller_connection(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        force_upgrade: bool = False,
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return the caller's container connection info.

        Args:
            user_id: The caller's user ID.
            bot_id: The bot ID.
            owner_id: The bot owner's ID.
            force_upgrade: If True, skip version check and force create/upgrade.
            iam_token: IAM token for caller authorization (reserved, currently unused).

        Returns:
            Dict with keys:
                - instance: The instance record from the database.
                - connection: WebSocket connection info (when need_poll=False).
                - need_poll: Whether the caller should poll for container ready.

        Raises:
            BotNotPublishedError: No success publish order for the service bot.
            ConnectionError: BaaS lifecycle or device resolution failure.
        """
        ...