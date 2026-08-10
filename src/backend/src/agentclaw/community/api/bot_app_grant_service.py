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

from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord


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

    def revoke(self, *, bot_id: str, user_id: str, app_id: int) -> None:
        """Withdraw ``user_id``'s delegation of ``app_id`` on ``bot_id``.

        Scoped to one delegating user: a collaborator withdrawing their own
        grant must not remove a colleague's delegation of the same application.

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

    def list_for_app(self, *, app_id: int, user_id: str) -> list[BotAppGrantRecord]:
        """The app's view — which bots may this app reach as ``user_id``.

        Names no bot and performs no bot-existence check: there is nothing to
        mask, since the result is one user's own delegations to one application.
        May include bots that user does not own.
        """


__all__ = ["BotAppGrantServiceProtocol"]
