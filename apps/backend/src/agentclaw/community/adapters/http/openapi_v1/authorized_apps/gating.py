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
    owner_id: str | None = None,
) -> tuple[str, int]:
    """Resolve the addressed bot and refuse a caller who may not operate it.

    Returns the resolved ``(owner_id, bot_pk)`` — the owner because the grant
    records it, and the primary key because the collaborator table is keyed on
    it.

    **The bot is addressed, not inferred.** ``bot_id`` alone does not identify a
    bot: ``ac_bots`` carries no unique key on it, and the retired ``default``
    convention gave many owners one. The pair does. So ``owner_id`` names whose
    bot this is and defaults to the caller's own — the same shape the
    engine-runtime operations already use, and the reason this group needed no
    special resolution of its own.

    An earlier revision of this function tried to *infer* the owner: read
    owner-scoped, fall back to an owner-blind read, and when that was ambiguous,
    narrow to the bots the caller could reach and adjudicate each candidate. It
    grew a bounded candidate query, an operability filter and a cap, and it
    still could not address the case it was built for — a caller who collaborates
    on two owners' same-named bots can operate both, and no amount of inference
    tells you which one they meant. Asking is not a workaround for that; it is
    the answer, and it deletes every one of those parts.

    Omitting ``owner_id`` is exactly the behaviour this group shipped with:
    ``get_bot(bot_id, caller)``, your own bot or nothing. Supplying it is the
    only new reach, and it is adjudicated rather than trusted.

    Both failures raise :class:`BotNotFoundError`, and that sameness is the
    security property rather than a convenience: a caller who may not operate
    the bot gets the answer a caller naming a nonexistent bot gets, byte for
    byte, so the surface never confirms a bot exists to someone with no business
    knowing. Naming an owner you have no relationship with therefore discloses
    nothing.

    **The refusals name no caller-supplied value.** Their message is carried
    into a log line verbatim by ``error_logging.log_public_error``, so a
    ``bot_id`` containing a percent-encoded newline would forge log lines on
    every refused request. The ids go to the log through
    :func:`~...log_safe.for_log` instead — escaped and bounded.
    """
    addressed_owner = owner_id or caller_id
    try:
        bot = bots.get_bot(bot_id, addressed_owner)
    except BotNotFoundError:
        # Re-raised with a server-authored message. The one it carries names the
        # bot id, and this one reaches a log line verbatim.
        logger.warning(
            "[authorized_apps] no such bot for the addressed owner: bot=%s owner=%s",
            for_log(bot_id),
            for_log(addressed_owner),
        )
        raise BotNotFoundError("Bot not found") from None
    resolved_owner = str(bot.get("owner_id") or "")
    bot_pk = int(bot.get("id") or 0)
    if not resolved_owner:
        # Unreachable for a well-formed row, and refused rather than trusted:
        # an empty owner would be written into the grant as the bot's owner and
        # would make the owner-override listing address nobody.
        logger.warning(
            "[authorized_apps] bot resolved with no owner, refusing: %s",
            for_log(bot_id),
        )
        raise BotNotFoundError("Bot not found")
    require_bot_operator(
        collaborators,
        bot=bot,
        bot_id=bot_id,
        caller_id=caller_id,
        owner_id=resolved_owner,
    )
    return resolved_owner, bot_pk


__all__ = ["resolve_delegable_bot"]
