"""Who may delegate their access to an application, and on which bot.

One helper, used by every bot-scoped operation in this group. The rule itself is
**not defined here** — it is ``core/engine_runtime/gate.py``'s
``require_bot_operator``, the same adjudication that decides who may drive a bot
— and reusing it rather than restating it is the point:

> You may delegate exactly the access you have.

An earlier revision of this surface confined delegation to a bot's owner, and
argued for it: handing a machine credential durable, human-free access to a bot
is not the same power as driving it. That argument does not survive contact with
how the platform is used. People routinely work on bots they do not own, and
under the owner-only rule an integration onboarded by such a person could reach
nothing — with the failure looking exactly like a missing grant.

What makes the wider rule safe is that a delegation is not a transfer. It is
bounded by the delegator's own access, that access is re-adjudicated on every
request the application makes, and nothing about it is copied into the record.
So a delegation confers no power its delegator does not already hold, and it
cannot outlive it: lose the collaboration, and the application loses the bot on
its next request, with no revocation and nothing to clean up.

The bot's owner is not left out of that. They see every grant standing against
their bot, whoever made it, and may withdraw any of them — see the router.
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.engine_runtime.gate import (
    OPERATOR_LEVEL,
    require_bot_operator,
    resolve_operator_level,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: How many same-named reachable bots :func:`_resolve_within_reach` will weigh.
#:
#: Generous rather than tight, because it is not a performance dial: every
#: candidate under it is adjudicated, and the only thing it bounds is how absurd
#: a collision has to be before the surface gives up. A person collaborating on
#: fifty distinct bots that all share one ``bot_id`` is past what a bare
#: ``bot_id`` can address at all.
#:
#: Small values are actively wrong here. The candidate rows are unordered, so a
#: cap that trims a realistic set decides the outcome by whichever rows the
#: database happened to return — and the caller's only operable bot is as likely
#: to be trimmed as any other.
CANDIDATE_CAP = 50


def resolve_delegable_bot(
    bots: Any,
    collaborators: Any,
    *,
    bot_id: str,
    caller_id: str,
) -> tuple[str, int]:
    """Resolve the bot and refuse a caller who may not operate it.

    Returns the resolved ``(owner_id, bot_pk)`` — the owner because the grant
    records it, and the primary key because the collaborator table is keyed on
    it (``bot_id`` alone is not unique across owners).

    Both failures raise :class:`BotNotFoundError`, and that sameness is the
    security property rather than a convenience: a caller who may not reach the
    bot gets the answer a caller naming a nonexistent bot gets, byte for byte,
    so the surface never confirms a bot exists to someone with no business
    knowing.

    **The owner is resolved owner-scoped first**, and that is not an
    optimisation. ``bot_id`` is not unique across owners — the legacy
    ``default`` convention gave many owners a bot of that id — so the owner-blind
    read fails closed on a duplicate rather than picking a row. Reaching for it
    first would turn three operations that work today into refusals for every
    owner of such a bot. Asking "is this the caller's own bot?" first answers
    exactly as it always did; the owner-blind read is the *fallback*, for the
    shared-bot case it exists to serve.

    **An ambiguous ``bot_id`` is resolved inside the caller's own reach**
    rather than refused. Failing closed on the duplicate was refusing the very
    caller this surface was built for: a member-level collaborator on someone
    else's legacy ``default`` bot misses the owner-scoped read (they do not own
    it) and trips the owner-blind one (another owner has a ``default`` too), so
    the bot they can plainly operate became unaddressable — the feature's whole
    point, lost to a name collision with a stranger's bot.

    Narrowing to the bots the caller owns or collaborates on breaks the tie,
    because the duplicates that make the id ambiguous tenant-wide are other
    people's. It cannot admit anyone: a candidate still has to pass the same
    operator adjudication, and a caller who reaches nothing sees the set go
    empty and gets the same masked refusal as before.

    Two operable candidates is the one case that stays refused, and it is a
    genuine ambiguity rather than a fail-closed default: the caller can operate
    both bots, ``bot_id`` is the only thing the request says, and it does not
    say which. Guessing would delegate the wrong bot silently.

    A caller who is neither the owner nor a collaborator raises
    :class:`BotNotFoundError` — the same masked refusal, since anything
    distinguishable tells a stranger that a bot exists.

    **The refusals name no caller-supplied value.** Their message is carried
    into a log line verbatim by ``error_logging.log_public_error``, so a
    ``bot_id`` containing a percent-encoded newline would forge log lines on
    every refused request. The id goes to the log through
    :func:`~...log_safe.for_log` instead — escaped and bounded.
    """
    try:
        bot = bots.get_bot(bot_id, caller_id)
    except BotNotFoundError:
        try:
            bot = bots.get_bot_by_id(bot_id)
        except BotLookupAmbiguousError:
            # Several live bots share this id and none is the caller's own.
            # Ask again inside the caller's reach — the duplicates are other
            # people's bots, and dropping them usually leaves exactly one.
            #
            # Letting the ambiguity escape is not an option either way: the
            # error is not in the surface's status map, so it would answer 500,
            # telling an unrelated caller that two bots share an id on a
            # surface whose whole refusal story is that a stranger learns
            # nothing.
            bot = _resolve_within_reach(
                bots, collaborators, bot_id=bot_id, caller_id=caller_id
            )
    resolved_owner = str(bot.get("owner_id") or "")
    bot_pk = int(bot.get("id") or 0)
    require_bot_operator(
        collaborators,
        bot_pk=bot_pk,
        bot_id=bot_id,
        caller_id=caller_id,
        owner_id=resolved_owner,
    )
    if not resolved_owner:
        # Unreachable for a well-formed row, and refused rather than trusted:
        # an empty owner would be written into the grant as the bot's owner and
        # would make the owner-override listing address nobody.
        logger.warning(
            "[authorized_apps] bot resolved with no owner, refusing: %s",
            for_log(bot_id),
        )
        raise BotNotFoundError("Bot not found")
    return resolved_owner, bot_pk


def _resolve_within_reach(
    bots: Any,
    collaborators: Any,
    *,
    bot_id: str,
    caller_id: str,
) -> dict[str, Any]:
    """The one bot with this id the caller may operate, or a masked refusal.

    Reachability is not operability — the candidate query admits a collaborator
    at *any* level, and the operator bar is ``MEMBER``. So the candidates are
    filtered by the same adjudication the caller would face anyway, and the
    count that matters is of bots the caller can actually operate. A viewer on
    one ``default`` and a member on another is not ambiguous; there is one
    answer and this finds it.

    Every reachable candidate is weighed, up to :data:`CANDIDATE_CAP`, and
    going over it refuses rather than deciding from the ones that came back.
    The rows are unordered, so trimming them chooses arbitrarily — and the row
    trimmed can be the only operable one, which turns this function back into
    the silent 404 it was written to remove.

    All three refusals are :class:`BotNotFoundError`, matching every other
    refusal here: a caller who reaches none of the duplicates must not be able
    to tell that any of them exist, and one who reaches several must not learn
    how many from the shape of the error.
    """
    candidates = bots.list_bots_reachable_by_id(
        bot_id, caller_id, CANDIDATE_CAP + 1
    )
    if len(candidates) > CANDIDATE_CAP:
        # More same-named bots than this is willing to adjudicate. Refused on
        # the *count*, before looking at any of them, because the rows come back
        # unordered: answering from them would mean deciding from an arbitrary
        # subset, and the row dropped could be the only one the caller can
        # operate. That failure is invisible — a masked 404 for someone who
        # really may delegate — and it is the failure this whole function was
        # added to remove, so reintroducing it through the bound would be worse
        # than refusing.
        logger.warning(
            "[authorized_apps] more than %d reachable bots share this id; "
            "refusing rather than deciding from a truncated set: %s",
            CANDIDATE_CAP,
            for_log(bot_id),
        )
        raise BotNotFoundError("Bot not found") from None
    operable = [
        bot
        for bot in candidates
        if resolve_operator_level(
            collaborators,
            bot_pk=int(bot.get("id") or 0),
            caller_id=caller_id,
            owner_id=str(bot.get("owner_id") or ""),
        )
        >= OPERATOR_LEVEL
    ]
    if len(operable) == 1:
        return operable[0]
    logger.warning(
        "[authorized_apps] ambiguous bot id resolved to %d operable bots for "
        "the caller, refusing as not-found: %s",
        len(operable),
        for_log(bot_id),
    )
    raise BotNotFoundError("Bot not found") from None


__all__ = ["resolve_delegable_bot"]
