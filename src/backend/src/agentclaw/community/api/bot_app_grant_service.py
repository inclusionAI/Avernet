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
        owner_id: str,
        app_id: int,
        app_name: str,
    ) -> BotAppGrantRecord:
        """Authorize ``app_id`` to reach ``bot_id``, and record that it happened.

        ``owner_id`` is the resolved bot owner and ``app_id`` comes off the
        verified App principal — neither is a value the request chose.

        Idempotent: repeating a live authorization returns it unchanged rather
        than failing, so a partner retrying a timed-out request is not punished
        for one that succeeded.
        """

    def revoke(self, *, bot_id: str, owner_id: str, app_id: int) -> None:
        """Withdraw ``app_id``'s authorization for ``bot_id``.

        Raises ``GrantNotFoundError`` when no live authorization matched, so the
        adapter can answer 404 distinctly from a successful withdrawal.
        """

    def list_for_bot(self, *, bot_id: str, owner_id: str) -> list[BotAppGrantRecord]:
        """The owner's view — which apps may reach this bot. Live only."""

    def list_for_app(self, *, app_id: int, owner_id: str) -> list[BotAppGrantRecord]:
        """The app's view — which of this owner's bots may this app reach.

        Names no bot and performs no bot-existence check: there is nothing to
        mask, since the result is the caller's own authorizations over their own
        bots.
        """


__all__ = ["BotAppGrantServiceProtocol"]
