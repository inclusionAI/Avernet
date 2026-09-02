"""Repository contract for user-granted account-level authorizations.

Every member is ``@abstractmethod``: an implementation that omits one fails at
construction naming the missing member. Domain imports are
``TYPE_CHECKING``-only — see ``core/repository/README.md``.

One user id, not two. The bot-level contract (``app_grant.py``) carries a
delegating user and a bot owner because a bot may be shared; an account-level
authorization is about the user's own account, so ``user_id`` is the whole
scope. Mutations each own their history write: a grant and its ``granted``
event land together or not at all.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.user_app_grant.models import UserAppGrantRecord


class UserAppGrantRepositoryProtocol(Protocol):
    """Live user→app account-level authorizations, plus the append-only history.

    No method carries an ``active`` qualifier: the live table holds nothing
    else, so "active" is the table's meaning rather than a filter a caller
    could forget to apply.
    """

    @abstractmethod
    def grant(self, data: Dict[str, Any]) -> UserAppGrantRecord:
        """Authorize an app to act as a user, and record that it happened.

        Inserts the live row and appends a ``granted`` event **in one
        transaction**. Idempotent under concurrency as well as in sequence:
        when a live row already exists it is returned untouched and nothing is
        appended, and a caller that loses the insert race receives the
        winner's row rather than a constraint error.

        Args:
            data: ``app_id``, ``app_name``, ``user_id``. Neither ``env`` nor the
                tenant is accepted: the tenant guard stamps the tenant from the
                request context, and ``env`` is always the running process's.

        Returns:
            The live authorization, new or pre-existing.
        """

    @abstractmethod
    def revoke(self, user_id: str, app_id: int) -> bool:
        """Withdraw one user's account-level authorization of one app.

        Deletes the live row and appends a ``revoked`` event in one
        transaction. The row is hard-deleted rather than flagged: the log
        outlives it, so the closed period survives.

        Returns:
            ``False`` when no live row matched, so the adapter can answer 404
            distinctly from a successful withdrawal.
        """

    @abstractmethod
    def list_for_user(self, user_id: str) -> List[UserAppGrantRecord]:
        """The user's view — every app that may act as them at the account level."""

    @abstractmethod
    def find(self, user_id: str, app_id: int) -> Optional[UserAppGrantRecord]:
        """One live authorization, or ``None`` when the app may not act as this user.

        The admission probe for the machine-caller path: a unique-key point
        lookup. ``None`` is a real state of the contract — "not authorized" is
        the answer this exists to give.
        """
