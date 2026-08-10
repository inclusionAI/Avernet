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
- **It does not check who may manage the bot.** The caller adjudicates that
  first — a user who cannot *operate* the bot never reaches this service. That
  adjudication answers a stranger exactly as it answers a caller naming a bot
  that does not exist, and re-deciding it here would risk a second, different
  answer.

What is left is the part that is genuinely this layer's: what a grant means when
one already exists, and what a withdrawal means when one does not.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.bot_app_grant.errors import (
    GrantIdentityTooLongError,
    GrantNotFoundError,
)
from agentclaw.community.core.bot_app_grant.models import (
    APP_NAME_MAX_LENGTH,
    IDENTITY_MAX_LENGTH,
    BotAppGrantRecord,
)
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
        user_id: str,
        owner_id: str,
        app_id: int,
        app_name: str,
    ) -> BotAppGrantRecord:
        """Authorize ``app_id`` to act as ``user_id`` on ``bot_id``.

        ``user_id`` is the delegating user — whoever is lending their access —
        and ``owner_id`` is the **resolved** owner of the bot, which is a
        different person whenever the bot is shared. Both are resolved by the
        caller and ``app_id`` comes off the verified App principal, so none of
        the three is a value the request chose. That is what stops a grant being
        pointed at someone else's bot, at another application, or at an
        authority the delegator does not have.

        Nothing about the delegator's permission level is recorded. The bound on
        what the application may do is that user's *live* access, re-adjudicated
        on every request; a level copied in here would keep saying "yes" after
        they lost it.

        Repeating a live grant returns it unchanged rather than failing: the
        caller asked for a state that already holds, and a partner retrying a
        timed-out request should not get an error for a request that succeeded.

        ``app_name`` is truncated to :data:`APP_NAME_MAX_LENGTH` here rather
        than at the column. The gateway does not bound it, so some valid name
        exceeds any width this table could pick; deciding it in code makes the
        outcome identical on every engine and in every SQL mode, instead of a
        rejected grant under strict settings and a silent truncation under
        permissive ones. The authorization is what matters, and it does not
        depend on the display name — see the note on the constant.
        """
        for label, value in (("user_id", user_id), ("owner_id", owner_id)):
            if len(value) > IDENTITY_MAX_LENGTH:
                # Refused, not truncated. See IDENTITY_MAX_LENGTH: truncating an
                # identity produces a grant that can never be found, which is a
                # silent authorization failure rather than a visible one.
                raise GrantIdentityTooLongError(
                    f"{label} exceeds {IDENTITY_MAX_LENGTH} characters, which a "
                    "grant cannot store or later resolve"
                )
        return self._repository.grant(
            {
                "app_id": app_id,
                "app_name": app_name[:APP_NAME_MAX_LENGTH],
                "bot_id": bot_id,
                "user_id": user_id,
                "owner_id": owner_id,
            }
        )

    def revoke(
        self, *, bot_id: str, user_id: str, owner_id: str, app_id: int
    ) -> None:
        """Withdraw ``user_id``'s delegation of ``app_id`` on ``bot_id``.

        Scoped to one delegating user, because a delegation is theirs to
        withdraw. A collaborator revoking their own grant must not remove a
        colleague's delegation of the same application on the same bot — those
        are two separate loans. The bot's *owner* has a wider withdrawal, which
        is :meth:`revoke_app` rather than this.

        ``owner_id`` is the resolved owner of the addressed bot — which bot this
        is, not who may withdraw it. See the repository contract for why a
        deletion cannot key on ``bot_id`` alone.

        Raises:
            GrantNotFoundError: no live authorization matched. Distinct from a
                successful withdrawal on purpose — someone reconciling their own
                records needs "there was nothing to remove" to read differently
                from "removed".
        """
        if not self._repository.revoke(bot_id, user_id, app_id, owner_id):
            raise GrantNotFoundError(
                f"no live authorization for app {app_id} on bot {bot_id}"
            )
        logger.info(
            "[bot_app_grant] user=%s revoked app_id=%s on bot_id=%s",
            user_id,
            app_id,
            bot_id,
        )

    def revoke_app(self, *, bot_id: str, owner_id: str, app_id: int) -> None:
        """Withdraw **every** delegation of ``app_id`` on ``bot_id``.

        The bot owner's override. An owner asking to revoke an application's
        access to their bot means all of it: a withdrawal that left the
        application still reaching the bot through a colleague's grant would not
        be a withdrawal at all.

        Raises:
            GrantNotFoundError: nothing was live to withdraw, so the adapter can
                answer 404 exactly as it does for the single-delegation form.
        """
        removed = self._repository.revoke_all_for_app_on_bot(bot_id, owner_id, app_id)
        if not removed:
            raise GrantNotFoundError(
                f"no live authorization for app {app_id} on bot {bot_id}"
            )
        logger.info(
            "[bot_app_grant] owner revoked all %s delegation(s) of app_id=%s "
            "on bot_id=%s",
            removed,
            app_id,
            bot_id,
        )

    def revoke_all_for_bot(self, *, bot_id: str, owner_id: str) -> int:
        """Withdraw every authorization against ``bot_id``, whoever delegated it.

        The bot-deletion sweep. Unlike the two withdrawals above this does not
        raise when nothing matched: deleting a bot that no application could
        reach is a perfectly ordinary deletion, and the caller is reporting a
        count rather than answering a request to remove one named thing.
        """
        return self._repository.revoke_all_for_bot(bot_id, owner_id)

    def list_for_bot(self, *, bot_id: str, owner_id: str) -> list[BotAppGrantRecord]:
        """The bot's view — every app that may reach it, and who let each in.

        Live authorizations only, which the live table gives for free: it holds
        nothing else, so there is no filter to forget.

        Deliberately **not** narrowed to one delegating user. The bot's owner has
        to be able to see a grant a collaborator made, or machine access to their
        own bot would be invisible to them — and invisible access is the failure
        the whole record exists to prevent. A caller wanting one user's view
        filters what comes back.

        ``owner_id`` is not that narrowing. It names *which* bot: ``bot_id``
        alone is not unique across owners, so without it this would show one
        owner what is authorized on a stranger's same-named bot.
        """
        return self._repository.list_for_bot(bot_id, owner_id)

    def find(
        self, *, bot_id: str, user_id: str, app_id: int
    ) -> BotAppGrantRecord | None:
        """The live delegation for this scope, or ``None``.

        The authorization probe the machine-caller path runs on every
        bot-scoped request. A unique-key point lookup, because the delegating
        user travels on the request rather than having to be discovered.

        ``None`` is a real state of the contract — "this application may not act
        as this user on this bot" is the answer it exists to give.

        **A record is not permission to proceed.** It says the delegation
        exists; whether that user may still operate that bot is a separate, live
        question for the collaborator gate, and the caller must ask it. That
        separation is what stops a delegation outliving the access it lends.
        """
        return self._repository.find(bot_id, user_id, app_id)

    def list_for_app(self, *, app_id: int, user_id: str) -> list[BotAppGrantRecord]:
        """The app's view — which bots may this app reach as ``user_id``.

        Names no bot, so there is nothing to *mask* — the result is one user's
        own delegations to one application, and an empty list discloses nothing.
        That is why this operation, alone among the reads, does not inherit the
        masked refusal from a named-bot resolve.

        It does still have to answer honestly, and a grant outliving its bot
        would make it lie. The deletion sweep now revokes on the way out, so
        this filter is the second line rather than the only one: it still
        matters for a bot deleted by a path that bypassed the sweep, and it
        costs one id-only query.

        **Filtered by ``(bot_id, owner_id)``, not by the bare id.** ``bot_id``
        is not unique across owners, so an id-only liveness check reports a
        deleted bot as live whenever another owner still holds one of the same
        id — advertising a grant whose bot is gone. The owner is on the record.

        **And not by the delegator's own bots.** The obvious filter —
        ``list_live_bot_ids_by_owner(user_id)`` — is what this did while a grant
        could only name the delegator's own bot, and it became wrong the moment
        a collaborator could delegate: a granted bot the user does not own is
        live and reachable, and filtering against that user's own bots would
        drop every one of them. Silently, and precisely in the case the feature
        exists to serve.

        **Two queries, not one per grant.** An earlier revision checked each
        grant with its own lookup and justified it as "bounded by design"; that
        bound was not real, and this route is unpaginated and calls a
        synchronous service, so the round trips would have blocked the event
        loop in proportion to the grant count.
        """
        granted = self._repository.list_for_app(app_id, user_id)
        if not granted:
            # Skip the bot query entirely when there is nothing to filter.
            return []
        live = self._bots.filter_live_bots(
            [(record.bot_id, record.owner_id) for record in granted]
        )
        return [
            record
            for record in granted
            if (record.bot_id, record.owner_id) in live
        ]


__all__ = ["BotAppGrantService"]
