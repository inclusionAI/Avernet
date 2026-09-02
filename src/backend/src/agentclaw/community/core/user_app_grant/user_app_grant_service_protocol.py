"""Service API Protocol for user-granted account-level authorizations.

Declares **real signatures**, not ``*args/**kwargs``, so
``tests/community/architecture/test_service_api_conformance.py`` can assert full
signature equality against ``UserAppGrantService``. Keep the two in step.

The public router and the admission seam depend on this Protocol rather than
on the concrete service: a delivery adapter reaches core through a Service API
that is separately reviewable and separately testable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.user_app_grant.models import UserAppGrantRecord


@runtime_checkable
class UserAppGrantServiceProtocol(Protocol):
    """Grant, withdraw and read user→app account-level authorizations."""

    def grant(self, *, user_id: str, app_id: int, app_name: str) -> UserAppGrantRecord:
        """Authorize ``app_id`` to act as ``user_id`` at the account level.

        ``user_id`` is the verified caller and ``app_id`` comes off the verified
        App principal, so neither is a value the request chose.

        Idempotent: repeating a live authorization returns it unchanged, with
        its original start time.
        """

    def revoke(self, *, user_id: str, app_id: int) -> None:
        """Withdraw ``user_id``'s account-level authorization of ``app_id``.

        Raises ``UserGrantNotFoundError`` when no live authorization matched,
        so the adapter can answer 404 distinctly from a successful withdrawal.
        """

    def list_for_user(self, *, user_id: str) -> list[UserAppGrantRecord]:
        """The user's view — every application that may act as them."""

    def find(self, *, user_id: str, app_id: int) -> UserAppGrantRecord | None:
        """The live authorization for this pair, or ``None`` when there is none.

        The admission probe for an application caller on a ``USER_GATED``
        operation. ``None`` is a real state — "may not act as this user" — not
        a widened type.
        """


__all__ = ["UserAppGrantRecord", "UserAppGrantServiceProtocol"]
