"""Startup-script helpers for the ``/openapi/v1/bots`` group (issue #926).

The three ``{bot_id}/startup-script`` handlers stay in ``router.py`` with the
rest of the group; the logic they share lives here. Split out when the router
crossed the 1000-line module cap — this is the part of it that is one cohesive
concern.

Nothing here touches FastAPI. These are plain functions over a bot record and
the startup-script Service API Protocol, which is what makes them testable
without a client.
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.api.bot_startup_script_service import (
    SUPPORTED,
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)

from .schemas import StartupScript


def _startup_script_target(
    bot: dict[str, Any],
    startup_script_service: BotStartupScriptServiceProtocol,
) -> tuple[str, str, str]:
    """Resolve ``(entity_id, support_state, reason)`` for a bot.

    ``entity_id`` is a storage key resolved here from the bot record — it is
    never a request parameter or a response field, per the group contract.
    """
    entity_id = bot.get("entity_id")
    if not entity_id:
        raise BotNotFoundError("bot has no associated entity")
    state, reason = startup_script_service.resolve_support(bot)
    return entity_id, state, reason


def _startup_script_payload(
    bot_id: str,
    record: Any,
    state: str,
    reason: str,
) -> StartupScript:
    """Shape a stored record — or its absence — as the response model."""
    return StartupScript(
        bot_id=bot_id,
        script=record.script if record is not None else "",
        size_bytes=record.size_bytes if record is not None else 0,
        updated_by=record.modifier if record is not None else "",
        updated_at=record.gmt_modified if record is not None else None,
        supported=state == SUPPORTED,
        unsupported_reason=reason,
    )
