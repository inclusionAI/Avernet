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
from agentclaw.community.core.engine_runtime.gate import require_bot_operator
from agentclaw.community.log import get_logger

logger = get_logger()


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

    A caller who is neither the owner nor a collaborator, and an ambiguous
    ``bot_id`` with no owner-scoped match, both raise
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
            # Several live bots share this id and none is the caller's. Refusing
            # as "not found" rather than letting the RuntimeError escape: it is
            # not in the surface's status map, so it would answer 500 — telling
            # an unrelated caller that two bots share an id, on a surface whose
            # whole refusal story is that a stranger learns nothing.
            logger.warning(
                "[authorized_apps] ambiguous bot id, refusing as not-found: %s",
                for_log(bot_id),
            )
            raise BotNotFoundError("Bot not found") from None
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


__all__ = ["resolve_delegable_bot"]
