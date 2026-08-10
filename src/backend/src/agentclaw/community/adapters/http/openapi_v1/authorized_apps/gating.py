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

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.engine_runtime.gate import require_bot_operator


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

    ``bots.get_bot_by_id`` decides nothing on its own — it answers "which bot,
    and whose". The adjudication below is what turns that into an answer about
    *this* caller, and running it here means no operation in this group can
    forget to.
    """
    bot = bots.get_bot_by_id(bot_id)
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
        raise BotNotFoundError(f"Bot not found: {bot_id}")
    return resolved_owner, bot_pk


__all__ = ["resolve_delegable_bot"]
