"""Whether a bot is reachable by more than its owner.

One predicate, deliberately, because two public surfaces gate on it and they
must agree: the sessions group
(``adapters/http/openapi_v1/.../sessions/router.py``) and the connection
endpoint (:mod:`agentclaw.community.core.engine_runtime.connection`). A bot
that is refused a session list but handed an operator socket is a 501 on the
front door with the window left open, so the rule lives here rather than being
restated at each gate.

Why the question is not ``bot_type == "personal"``: that is the *default*
answer, not the rule. ``ac_bots.public`` is set with no ``bot_type`` gate
(``bot_public_service``), and a coding app — ``active_engine == "claude_code"``
with ``template_type == "applicationCoding"`` — takes collaborators through the
branch that otherwise requires a ``service`` bot
(``collaborator_service.add_collaborator``). ``ExpertChatService`` admits
owner, public and collaborator callers alike (``_check_chat_access``) and
creates each one's sessions on the bot's own binding.
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_collaborator.repository.protocol import (
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

#: ``ac_bots.public`` is a ``String(64)``, not a boolean.
_PUBLIC = "1"


def bot_is_shared(
    bot: dict[str, Any],
    collaborator_repo: CollaboratorRepositoryProtocol,
    *,
    bot_id: str,
    owner_id: str,
) -> bool:
    """Whether any caller but the owner can reach this bot's device.

    ``bot`` is a ``BotService.get_bot`` record, already resolved owner-scoped —
    ``bot_id`` and ``owner_id`` must come from that record, not from the
    request, since ``bot_id`` is not unique across owners.

    Synchronous: one indexed read, and only when the bot is not already public.
    Callers on an event loop run it in a worker thread with the rest of their
    resolution.

    A failed collaborator lookup counts as **shared**. This feeds a gate that
    refuses a surface, so the direction of the guess decides what a database
    blip does: reading an unavailable collaborator table as "no collaborators"
    would open a shared bot's sessions to its owner at exactly the moment the
    check meant to prevent that could not run.
    """
    if str(bot.get("public") or "0") == _PUBLIC:
        return True
    try:
        return bool(
            collaborator_repo.list_by_bot(
                bot_id=bot_id, owner_id=owner_id, env=get_current_env()
            )
        )
    except Exception:
        logger.exception(
            "[engine_runtime] collaborator lookup failed for bot=%s; "
            "treating the bot as shared",
            bot_id,
        )
        return True


__all__ = ["bot_is_shared"]
