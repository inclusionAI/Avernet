"""Service API Protocol for owner-granted bot authorizations.

Declares **real signatures**, not ``*args/**kwargs``, so
``tests/community/architecture/test_service_api_conformance.py`` can assert full
signature equality against ``BotAppGrantService`` — parameter names, kinds,
defaults, and coroutine status. Keep the two in step: a single changed default
fails that gate.

The public router depends on this Protocol rather than on the concrete service,
which is the boundary AGENTS.md asks for: a delivery adapter reaches core
through a Service API that is separately reviewable and separately testable, not
through one implementation it happens to import.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.bot_app_grant.models import (
    BotAppGrantRecord,
    UserAppGrantRecord,
)


@runtime_checkable
class BotAppGrantServiceProtocol(Protocol):
    """Grant, withdraw and read bot→app authorizations."""

    def grant(
        self,
        *,
        bot_id: str,
        user_id: str,
        owner_id: str,
        app_id: int,
        app_name: str,
    ) -> BotAppGrantRecord:
        """Authorize ``app_id`` to act as ``user_id`` on ``bot_id``, and record it.

        ``user_id`` is the delegating user whose access is being lent;
        ``owner_id`` is the resolved owner of the bot, a different person
        whenever the bot is shared. Both are resolved by the caller and
        ``app_id`` comes off the verified App principal, so none is a value the
        request chose.

        Idempotent: repeating a live authorization returns it unchanged rather
        than failing, so a partner retrying a timed-out request is not punished
        for one that succeeded.
        """

    def grant_for_creation(
        self,
        *,
        bot_id: str,
        user_id: str,
        owner_id: str,
        app_id: int,
        app_name: str,
    ) -> BotAppGrantRecord:
        """Authorize ``app_id`` on a bot it is creating as ``user_id``, now.

        The one grant written **before the bot is live**. An application
        admitted to a creation under a user-level delegation is granted the bot
        it creates at the moment the creation starts — so the poll that
        completes a pending creation, and every operation after it, finds an
        ordinary bot grant to check against. Written at start rather than at
        completion because for most of a creation's life there is no bot record
        yet, and the poll is bot-scoped.

        Everything else about the row is an ordinary bot grant: the owner sees
        it in the bot's listing and can withdraw it there, and the deletion
        sweep removes it with the bot. Idempotent like :meth:`grant`.
        """

    def revoke(
        self, *, bot_id: str, user_id: str, owner_id: str, app_id: int
    ) -> None:
        """Withdraw ``user_id``'s delegation of ``app_id`` on ``bot_id``.

        Scoped to one delegating user: a collaborator withdrawing their own
        grant must not remove a colleague's delegation of the same application.
        Scoped to the resolved ``owner_id`` too, because ``bot_id`` alone does
        not identify a bot and a mis-resolved delete is destructive.

        Raises ``GrantNotFoundError`` when no live authorization matched, so the
        adapter can answer 404 distinctly from a successful withdrawal.
        """

    def revoke_app(self, *, bot_id: str, owner_id: str, app_id: int) -> None:
        """Withdraw **every** delegation of ``app_id`` on ``bot_id``.

        The bot owner's override, for whom "revoke this app" means all of it.

        Raises ``GrantNotFoundError`` when nothing was live to withdraw.
        """

    def revoke_all_for_bot(self, *, bot_id: str, owner_id: str) -> int:
        """Withdraw every authorization against ``bot_id``. Returns the count.

        The bot-deletion sweep. Does not raise on an empty sweep: deleting a bot
        no application could reach is an ordinary deletion.
        """

    def list_for_bot(self, *, bot_id: str, owner_id: str) -> list[BotAppGrantRecord]:
        """The bot's view — every app that may reach it, and who let each in.

        Live only, and not narrowed to one delegating user: the bot's owner must
        be able to see a grant a collaborator made. ``owner_id`` names which bot,
        not which caller — ``bot_id`` is not unique across owners.
        """

    def find(
        self, *, bot_id: str, owner_id: str, user_id: str, app_id: int
    ) -> BotAppGrantRecord | None:
        """The live delegation for this scope, or ``None`` when there is none.

        The authorization probe for an application caller. ``None`` is a real
        state — "may not act as this user on this bot" — not a widened type.

        A record means the delegation exists, **not** that the request may
        proceed: whether the delegating user may still operate the bot is asked
        separately and live.
        """

    def list_for_app(self, *, app_id: int, user_id: str) -> list[BotAppGrantRecord]:
        """The app's view — which bots may this app reach as ``user_id``.

        Names no bot and performs no bot-existence check: there is nothing to
        mask, since the result is one user's own delegations to one application.
        May include bots that user does not own.
        """


@runtime_checkable
class UserAppGrantServiceProtocol(Protocol):
    """Grant, withdraw and read user→app delegations.

    The account-level record: *"app A may act as user U where no bot is
    addressed"*. It is what admits an application to a ``USER_DELEGATED``
    operation — one that acts for the named user before any bot exists — and
    it reaches no existing bot on its own.
    """

    def grant(
        self, *, user_id: str, app_id: int, app_name: str
    ) -> UserAppGrantRecord:
        """Authorize ``app_id`` to act as ``user_id`` where no bot is addressed.

        ``user_id`` is the verified caller and ``app_id`` comes off the verified
        App principal. Idempotent: repeating a live delegation returns it
        unchanged.
        """

    def revoke(self, *, user_id: str, app_id: int) -> None:
        """Withdraw ``user_id``'s delegation of ``app_id``.

        Raises ``GrantNotFoundError`` when no live delegation matched.
        """

    def find(self, *, user_id: str, app_id: int) -> UserAppGrantRecord | None:
        """The live delegation for this pair, or ``None`` when there is none."""

    def list_for_user(self, *, user_id: str) -> list[UserAppGrantRecord]:
        """The user's view — every application that may act as them."""


__all__ = ["BotAppGrantServiceProtocol", "UserAppGrantServiceProtocol"]
