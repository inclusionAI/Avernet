"""Domain policy for user-level application delegations.

Transport-agnostic (Rule 7), and as thin as the bot grant service for the same
reason: the tenant guard and the caller's adjudication already did the work.
What is left is what a grant means when one exists, what a withdrawal means when
one does not, and the one thing this record must refuse to store.

**What a user-level delegation lends, and what it does not.** It admits the
application to the operations the admission table marks ``USER_DELEGATED`` —
those that act for the user but address no bot, creation above all — and it
counts as the "some live delegation" the ``USER_GATED`` reads ask for. It does
**not** reach any existing bot: that stays the bot grant's, per bot, visible to
and withdrawable by the bot's owner. A bot the application creates under this
delegation is granted to it as an ordinary bot grant at creation time, so the
two records compose rather than overlap.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.bot_app_grant.bot_app_grant_service_protocol import (
    UserAppGrantServiceProtocol,
)
from agentclaw.community.core.bot_app_grant.errors import (
    GrantIdentityTooLongError,
    GrantNotFoundError,
)
from agentclaw.community.core.bot_app_grant.models import (
    APP_NAME_MAX_LENGTH,
    IDENTITY_MAX_LENGTH,
    UserAppGrantRecord,
)
from agentclaw.community.core.repository.protocols.bot import (
    UserAppGrantRepositoryProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class UserAppGrantService(UserAppGrantServiceProtocol):
    """Grant, withdraw and read user→app delegations."""

    @inject
    def __init__(self, repository: UserAppGrantRepositoryProtocol) -> None:
        self._repository = repository

    def grant(
        self, *, user_id: str, app_id: int, app_name: str
    ) -> UserAppGrantRecord:
        """Authorize ``app_id`` to act as ``user_id`` where no bot is addressed.

        ``user_id`` is the verified caller and ``app_id`` comes off the verified
        App principal, so neither is a value the request chose. Idempotent, and
        ``app_name`` is truncated rather than refused, both for the reasons the
        bot grant gives. An over-long ``user_id`` is refused rather than
        truncated: a truncated identity is a delegation no lookup can find.
        """
        if len(user_id) > IDENTITY_MAX_LENGTH:
            raise GrantIdentityTooLongError(
                f"user_id exceeds {IDENTITY_MAX_LENGTH} characters, which a "
                "delegation cannot store or later resolve"
            )
        return self._repository.grant(
            {
                "app_id": app_id,
                "app_name": app_name[:APP_NAME_MAX_LENGTH],
                "user_id": user_id,
            }
        )

    def revoke(self, *, user_id: str, app_id: int) -> None:
        """Withdraw ``user_id``'s delegation of ``app_id``.

        Withdraws the account-level consent only. Bot grants the application
        holds — including those written when it created bots under this
        delegation — are separate records and stay until withdrawn on the bot,
        where the bot's owner can see them.

        Raises:
            GrantNotFoundError: no live delegation matched.
        """
        if not self._repository.revoke(user_id, app_id):
            raise GrantNotFoundError(
                f"no live user-level delegation for app {app_id}"
            )
        logger.info(
            "[user_app_grant] user=%s revoked app_id=%s", user_id, app_id
        )

    def find(self, *, user_id: str, app_id: int) -> UserAppGrantRecord | None:
        """The live delegation for this pair, or ``None``.

        The admission probe for an application caller on a ``USER_DELEGATED``
        operation. ``None`` is the answer it exists to give.
        """
        return self._repository.find(user_id, app_id)

    def list_for_user(self, *, user_id: str) -> list[UserAppGrantRecord]:
        """The user's view — every application that may act as them."""
        return self._repository.list_for_user(user_id)


__all__ = ["UserAppGrantService"]
