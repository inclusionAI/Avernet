"""Repository contract for user-granted bot authorizations.

Every member is ``@abstractmethod``: an implementation that omits one fails at
construction naming the missing member, instead of raising ``AttributeError``
at the call site. Domain imports are ``TYPE_CHECKING``-only — see
``core/repository/README.md`` for why that direction is load-bearing.

**Two user ids, and confusing them is the mistake this contract exists to make
impossible.** A grant means "app A may act as user U on bot B, which O owns":

- ``user_id`` — the **delegating** user, whose access is being lent. Every
  authorization lookup and every scope keys on this, because the delegation
  belongs to them.
- ``owner_id`` — the bot's **owner**, carried so the machine-caller path can
  address the bot without a second query. Written at grant time, never a lookup
  key, and frequently a person with no relationship to the application at all.

Mutations each own their history write rather than leaving it to the service: a
grant and its ``granted`` event must land together or not at all, and a contract
that lets a caller perform half of that is one that will eventually be
half-performed.
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

    The read methods split on *which* question is being asked, and the split is
    deliberate:

    - :meth:`find` and :meth:`list_for_app` take ``user_id``, because they
      answer "what may this application do **as this person**" — the delegation
      is the subject.
    - :meth:`list_for_bot` and the two sweeps take ``owner_id`` but **not**
      ``user_id``, because they answer "what stands against this bot, whoever
      allowed it" — the bot is the subject, and narrowing to one delegator would
      hide from a bot's owner exactly the grants they most need to see.

    That surviving ``owner_id`` is *identity, not permission*. ``ac_bots``
    carries no unique key on ``bot_id`` — the legacy ``default`` convention gave
    many owners a bot of that id — so "this bot" is ``(bot_id, owner_id)``.
    Dropping it would let one owner read, and a deletion sweep destroy, grants
    belonging to a stranger's same-named bot.
    """

    @abstractmethod
    def grant(self, data: Dict[str, Any]) -> BotAppGrantRecord:
        """Authorize an app to act as a user on a bot, and record that it happened.

        Inserts the live row and appends a ``granted`` event **in one
        transaction**.

        Idempotent, and under concurrency as well as in sequence: when a live
        row already exists it is returned untouched and **nothing is appended**,
        and a caller that loses the insert race receives the winner's row rather
        than a constraint error. ``gmt_create`` therefore keeps answering "could
        reach this bot from T1" honestly, and a duplicate call does not invent
        an authorization period that never began.

        Args:
            data: ``app_id``, ``app_name``, ``bot_id``, ``user_id``,
                ``owner_id``. Neither ``env`` nor the tenant is accepted, and
                both omissions are deliberate: the tenant guard stamps the
                tenant from the request context and refuses a row naming
                another, and ``env`` is always the running process's. A
                caller-chosen ``env`` could write a row that is live, invisible
                to every read, and impossible to revoke while still occupying
                the unique key.

        Returns:
            The live authorization, new or pre-existing.
        """

    @abstractmethod
    def revoke(
        self, bot_id: str, user_id: str, app_id: int, owner_id: str
    ) -> bool:
        """Withdraw one user's delegation of one app on one bot.

        Deletes the live row and appends a ``revoked`` event in one
        transaction. The row is hard-deleted rather than flagged: the log
        outlives it, so the closed period survives without the live table
        having to model a state it has no room for.

        Scoped to ``user_id`` because a delegation is that user's to withdraw.
        A collaborator revoking "their" grant must not remove a colleague's
        delegation of the same application on the same bot — those are two
        separate loans of two separate authorities.

        Scoped to ``owner_id`` because that is *which bot* — ``bot_id`` is not
        unique across owners. Unlike :meth:`find`, this method cannot afford the
        collision: a read that resolves the wrong same-named bot fails safe,
        while a delete that does destroys a live authorization on a bot the
        caller never addressed.

        Returns:
            ``False`` when no live row matched, so the adapter can answer 404
            distinctly from a successful withdrawal.
        """

    @abstractmethod
    def revoke_all_for_app_on_bot(self, bot_id: str, owner_id: str, app_id: int) -> int:
        """Withdraw **every** user's delegation of one app on one bot.

        The bot owner's override. An owner asking to revoke an application's
        access to their bot means all of it, not the share one colleague
        happened to delegate — a withdrawal that left the application still
        reaching the bot through a second grant would not be a withdrawal.

        One ``revoked`` event per row removed, built from the live rows.

        Returns:
            How many authorizations were withdrawn; ``0`` when none matched.
        """

    @abstractmethod
    def revoke_all_for_bot(self, bot_id: str, owner_id: str) -> int:
        """Withdraw every authorization standing against a bot.

        The deletion sweep. Names no application and no delegating user: a
        deleted bot has no authorizations, whoever granted them. It does name
        the owner, because that is half of which bot is being deleted.

        One ``revoked`` event per row removed, and all of it in one
        transaction, so a partial sweep cannot leave a deleted bot reachable.

        Returns:
            How many authorizations were withdrawn.
        """

    @abstractmethod
    def list_for_bot(self, bot_id: str, owner_id: str) -> List[BotAppGrantRecord]:
        """The bot's view — every app that may reach it, and who let each in.

        Deliberately **not** scoped to a delegating user. The bot's owner has to
        be able to see a grant a collaborator made, or machine access to their
        own bot would be invisible to them; a caller wanting one user's grants
        filters the result.
        """

    @abstractmethod
    def list_for_app(self, app_id: int, user_id: str) -> List[BotAppGrantRecord]:
        """The app's view — which bots may this app reach **as this user**.

        Note this may include bots ``user_id`` does not own, which is the whole
        point of delegating a collaborator's access.
        """

    @abstractmethod
    def find(
        self, bot_id: str, user_id: str, app_id: int
    ) -> Optional[BotAppGrantRecord]:
        """One live authorization, or ``None`` when the app may not act as this user.

        The authorization probe for the machine-caller path: a unique-key point
        lookup, because the delegating user travels on the request rather than
        having to be discovered. It names no owner because it does not need to —
        the row it returns *carries* the owner, which is what tells the caller
        which same-named bot the delegation was made on.

        One consequence of the key, worth knowing: a single user cannot delegate
        one application to two bots that share a ``bot_id``. The second grant
        collides on the unique key and comes back as the first. Only reachable
        through the retired ``default`` convention, and it fails safe (the
        caller keeps the access they had) rather than resolving to the wrong
        bot.

        ``None`` is a real state of the contract — "not authorized" is the
        answer this exists to give — not a widened return type.

        A record coming back does **not** by itself mean the request may
        proceed. It means the delegation exists; whether that user may still
        operate that bot is a separate, live question for the collaborator gate,
        and the caller must ask it. This is what keeps the delegation from
        outliving the access it lends.
        """
