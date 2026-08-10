"""Domain policy for owner-granted bot authorizations.

Transport-agnostic (Rule 7): nothing here imports FastAPI, reads a request, or
knows what an HTTP status is. The adapter maps :class:`GrantNotFoundError` onto
a 404 and everything else onto the surface's envelopes.

Two things this service deliberately does **not** do, because doing them here
would mean doing them twice:

- **It does not check the tenant.** ``register_avernet_tenant_guard`` stamps the
  request's tenant on every insert and refuses one naming a different tenant,
  and confines every read to it. A comparison here would restate a guarantee
  already enforced a layer down, and would rot the moment someone trusted it
  instead of the guard.
- **It does not check who may manage the bot.** The caller resolves the bot
  through the owner-scoped read first; a non-owner never reaches this service.
  That read answers a stranger exactly as it answers a caller naming a bot that
  does not exist, and re-deciding it here would risk a second, different answer.

What is left is the part that is genuinely this layer's: what a grant means when
one already exists, and what a withdrawal means when one does not.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.bot_app_grant.errors import GrantNotFoundError
from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord
from agentclaw.community.core.repository.protocols.bot import (
    BotAppGrantRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class BotAppGrantService:
    """Grant, withdraw and read bot→app authorizations."""

    @inject
    def __init__(
        self,
        repository: BotAppGrantRepositoryProtocol,
        bots: BotRepository,
    ) -> None:
        self._repository = repository
        self._bots = bots

    def grant(
        self,
        *,
        bot_id: str,
        owner_id: str,
        app_id: int,
        app_name: str,
    ) -> BotAppGrantRecord:
        """Authorize ``app_id`` to reach ``bot_id``.

        ``owner_id`` is the **resolved** bot owner and ``app_id`` comes off the
        verified App principal — neither is a value the request chose, which is
        what stops a grant being pointed at someone else's bot or another
        application.

        Repeating a live grant returns it unchanged rather than failing: the
        caller asked for a state that already holds, and a partner retrying a
        timed-out request should not get an error for a request that succeeded.
        """
        return self._repository.grant(
            {
                "app_id": app_id,
                "app_name": app_name,
                "bot_id": bot_id,
                "owner_id": owner_id,
            }
        )

    def revoke(self, *, bot_id: str, owner_id: str, app_id: int) -> None:
        """Withdraw ``app_id``'s authorization for ``bot_id``.

        Raises:
            GrantNotFoundError: no live authorization matched. Distinct from a
                successful withdrawal on purpose — an owner reconciling their
                own records needs "there was nothing to remove" to read
                differently from "removed".
        """
        if not self._repository.revoke(bot_id, owner_id, app_id):
            raise GrantNotFoundError(
                f"no live authorization for app {app_id} on bot {bot_id}"
            )
        logger.info(
            "[bot_app_grant] owner=%s revoked app_id=%s on bot_id=%s",
            owner_id,
            app_id,
            bot_id,
        )

    def list_for_bot(self, *, bot_id: str, owner_id: str) -> list[BotAppGrantRecord]:
        """The owner's view — which apps may reach this bot.

        Live authorizations only, which the live table gives for free: it holds
        nothing else, so there is no filter to forget.
        """
        return self._repository.list_for_bot(bot_id, owner_id)

    def list_for_app(self, *, app_id: int, owner_id: str) -> list[BotAppGrantRecord]:
        """The app's view — which of this owner's bots may this app reach.

        Names no bot, so there is nothing to *mask* — the result is the caller's
        own authorizations over their own bots, and an empty list discloses
        nothing. That is why this operation, alone among the four, does not
        inherit the masked refusal from a named-bot resolve.

        It does still have to answer honestly, and a grant outliving its bot
        would make it lie. ``delete_bot`` soft-deletes the ``ac_bots`` row and
        does not touch grants, so without this filter a withdrawn-by-deletion
        bot would be reported as currently authorized indefinitely. Each grant
        is checked against a live bot for this owner —
        ``get_by_id_and_owner`` already excludes soft-deleted rows.

        One lookup per grant, and that is bounded by design: this is the set of
        bots *one* owner has authorized to *one* app, not a tenant-wide list.

        This filters the *report*; it does not revoke. Revoking on deletion is
        the fuller fix and belongs with bot lifecycle rather than here — until
        it exists, the row survives and only stops being advertised.
        """
        granted = self._repository.list_for_app(app_id, owner_id)
        return [
            record
            for record in granted
            if self._bots.get_by_id_and_owner(record.bot_id, owner_id) is not None
        ]


__all__ = ["BotAppGrantService"]
