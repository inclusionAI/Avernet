"""Domain policy for user-granted account-level authorizations.

Transport-agnostic (Rule 7): nothing here imports FastAPI, reads a request, or
knows what an HTTP status is. The adapter maps :class:`UserGrantNotFoundError`
onto a 404 and everything else onto the surface's envelopes.

This service does **not** check the tenant — ``register_avernet_tenant_guard``
stamps the request's tenant on every insert, refuses one naming a different
tenant, and confines every read to it. A comparison here would restate a
guarantee enforced a layer down.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.bot import (
    UserAppGrantRepositoryProtocol,
)
from agentclaw.community.core.user_app_grant.errors import (
    UserGrantIdentityTooLongError,
    UserGrantNotFoundError,
)
from agentclaw.community.core.user_app_grant.models import (
    APP_NAME_MAX_LENGTH,
    IDENTITY_MAX_LENGTH,
    UserAppGrantRecord,
)
from agentclaw.community.core.user_app_grant.user_app_grant_service_protocol import (
    UserAppGrantServiceProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class UserAppGrantService(UserAppGrantServiceProtocol):
    """Grant, withdraw and read user→app account-level authorizations."""

    @inject
    def __init__(self, repository: UserAppGrantRepositoryProtocol) -> None:
        self._repository = repository

    def grant(self, *, user_id: str, app_id: int, app_name: str) -> UserAppGrantRecord:
        """Authorize ``app_id`` to act as ``user_id`` at the account level.

        ``user_id`` is the verified caller and ``app_id`` comes off the verified
        App principal — neither is a value the request chose, which is what
        stops a grant being pointed at another person or another application.

        Repeating a live grant returns it unchanged rather than failing, so a
        partner retrying a timed-out request is not punished for one that
        succeeded. ``app_name`` is truncated here rather than at the column,
        for the reason the bot-level service gives: the outcome is then the
        same on every engine and in every SQL mode.
        """
        if len(user_id) > IDENTITY_MAX_LENGTH:
            raise UserGrantIdentityTooLongError(
                f"user_id exceeds {IDENTITY_MAX_LENGTH} characters, which a "
                "grant cannot store or later resolve"
            )
        return self._repository.grant(
            {
                "app_id": app_id,
                "app_name": app_name[:APP_NAME_MAX_LENGTH],
                "user_id": user_id,
            }
        )

    def revoke(self, *, user_id: str, app_id: int) -> None:
        """Withdraw ``user_id``'s account-level authorization of ``app_id``.

        Raises:
            UserGrantNotFoundError: no live authorization matched. Distinct from
                a successful withdrawal on purpose — someone reconciling their
                records needs "there was nothing to remove" to read differently
                from "removed".
        """
        if not self._repository.revoke(user_id, app_id):
            raise UserGrantNotFoundError(
                f"no live account-level authorization for app {app_id}"
            )
        logger.info(
            "[user_app_grant] user=%s revoked app_id=%s at the account level",
            user_id,
            app_id,
        )

    def list_for_user(self, *, user_id: str) -> list[UserAppGrantRecord]:
        """The user's view — every application that may act as them.

        Live authorizations only, which the live table gives for free: it
        holds nothing else, so there is no filter to forget.
        """
        return self._repository.list_for_user(user_id)

    def find(self, *, user_id: str, app_id: int) -> UserAppGrantRecord | None:
        """The live authorization for this pair, or ``None``.

        The admission probe the machine-caller path runs on every
        ``USER_GATED`` request: one unique-key lookup, and the only thing
        standing between that caller and the operation.
        """
        return self._repository.find(user_id, app_id)


__all__ = ["UserAppGrantService"]
