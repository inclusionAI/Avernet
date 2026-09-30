"""Repository contract for user-level application delegations.

The account-level sibling of ``app_grant.py``. A row means *"app A may act as
user U where no bot is addressed"* — the consent a creation is admitted on,
since a bot grant cannot name a bot that does not exist yet.

Every member is ``@abstractmethod`` and domain imports are ``TYPE_CHECKING``
only, for the reasons ``core/repository/README.md`` gives. Mutations own their
history write: a grant and its ``granted`` event land together or not at all.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import List, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.bot_app_grant.models import UserAppGrantRecord


class UserAppGrantRepositoryProtocol(Protocol):
    """Live user→app delegations, plus the append-only history behind them.

    Keyed on ``(app_id, user_id)`` within the tenant and env. There is no owner
    and no bot anywhere in this contract, and adding one would turn it back
    into the bot grant — the record exists precisely for the operations that
    have neither.
    """

    @abstractmethod
    def grant(
        self, *, app_id: int, app_name: str, user_id: str
    ) -> UserAppGrantRecord:
        """Record a delegation, appending a ``granted`` event.

        Idempotent: a delegation that is already live is returned unchanged
        rather than duplicated or failed, under concurrency as well as in
        sequence.
        """

    @abstractmethod
    def revoke(self, user_id: str, app_id: int) -> bool:
        """Withdraw one delegation, appending a ``revoked`` event.

        Returns whether a live row was removed, so the service can answer
        "there was nothing to remove" distinctly from "removed".
        """

    @abstractmethod
    def find(self, user_id: str, app_id: int) -> Optional[UserAppGrantRecord]:
        """The live delegation for this pair, or ``None``."""

    @abstractmethod
    def list_for_user(self, user_id: str) -> List[UserAppGrantRecord]:
        """The user's view — every application that may act as them."""
