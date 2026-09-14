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

    async def get_application_caller_connection(
        self,
        *,
        app_id: int,
        tenant: str,
        user_id: str,
        bot_id: str,
        owner_id: str,
        force_upgrade: bool = False,
    ) -> Dict[str, Any]:
        """Allow any verified application to operate an existing caller instance.

        ``app_id`` and ``tenant`` must come from a verified application identity.
        The active tenant must match before any repository access. Require a
        Bot in that tenant. No grant or owner/public/member access is required:
        an application may target another user's instance even on a private Bot.
        Only an existing instance with a nonempty bot UUID may enter the usual
        lifecycle, including force-upgrade and polling. No admin privilege or
        first-time provisioning is granted. Raise ChatPermissionError on denial;
        otherwise return the same instance/connection/need_poll dictionary as
        get_caller_connection. Existing lifecycle errors propagate unchanged.
        """
        ...

    async def get_authorized_caller_connection(
        self,
        *,
        operator_id: str,
        user_id: str,
        bot_id: str,
        owner_id: str,
        is_super_admin: bool,
        force_upgrade: bool = False,
    ) -> Dict[str, Any]:
        """Authorize an actor before entering caller lifecycle operations."""
        ...

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