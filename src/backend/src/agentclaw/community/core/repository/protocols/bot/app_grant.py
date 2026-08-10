"""Repository contract for owner-granted bot authorizations.

Every member is ``@abstractmethod``: an implementation that omits one fails at
construction naming the missing member, instead of raising ``AttributeError``
at the call site. Domain imports are ``TYPE_CHECKING``-only — see
``core/repository/README.md`` for why that direction is load-bearing.

Two writes, two reads, one lookup. The two mutations each own their history
write rather than leaving it to the service: a grant and its ``granted`` event
must land together or not at all, and a contract that lets a caller perform
half of that is a contract that will eventually be half-performed.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord


class BotAppGrantRepositoryProtocol(Protocol):
    """Live bot→app authorizations, plus the append-only history behind them.

    No method carries an ``active`` qualifier: the live table holds nothing
    else, so "active" is the table's meaning rather than a filter a caller
    could forget to apply.

    Both list methods take ``owner_id``. The scoping lives in the contract, so
    no implementation and no caller can return a row belonging to someone other
    than the owner on the call.
    """

    @abstractmethod
    def grant(self, data: Dict[str, Any]) -> BotAppGrantRecord:
        """Authorize an app for a bot, and record that it happened.

        Inserts the live row and appends a ``granted`` event **in one
        transaction**.

        Idempotent: when a live row already exists it is returned untouched and
        **nothing is appended**. ``gmt_create`` therefore keeps answering "could
        reach this bot from T1" honestly, and a duplicate call does not invent
        an authorization period that never began.

        Args:
            data: ``app_id``, ``app_name``, ``bot_id``, ``owner_id``; ``env``
                optional and defaulted. The tenant is **not** passed — the
                tenant guard stamps it from the request context and refuses a
                row naming another tenant.

        Returns:
            The live authorization, new or pre-existing.
        """

    @abstractmethod
    def revoke(self, bot_id: str, owner_id: str, app_id: int) -> bool:
        """Withdraw an authorization, and record that it happened.

        Deletes the live row and appends a ``revoked`` event in one
        transaction. The row is hard-deleted rather than flagged: the log
        outlives it, so the closed period survives without the live table
        having to model a state it has no room for.

        Returns:
            ``False`` when no live row matched, so the adapter can answer 404
            distinctly from a successful withdrawal.
        """

    @abstractmethod
    def list_for_bot(self, bot_id: str, owner_id: str) -> List[BotAppGrantRecord]:
        """The owner's view — which apps may reach this bot."""

    @abstractmethod
    def list_for_app(self, app_id: int, owner_id: str) -> List[BotAppGrantRecord]:
        """The app's view — which of this owner's bots may this app reach."""

    @abstractmethod
    def find(
        self, bot_id: str, owner_id: str, app_id: int
    ) -> Optional[BotAppGrantRecord]:
        """One live authorization, or ``None`` when the app may not reach the bot.

        ``None`` is a real state of the contract — "not authorized" is the
        answer this exists to give — not a widened return type.
        """
