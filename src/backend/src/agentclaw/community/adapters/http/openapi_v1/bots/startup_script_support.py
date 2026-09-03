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

from typing import Any, Optional

from agentclaw.community.api.bot_service import BotServiceProtocol
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


def _withdraw_the_write_if_the_bot_was_deleted(
    bot_id: str,
    entity_id: str,
    owner_id: str,
    bot_service: BotServiceProtocol,
    startup_script_service: BotStartupScriptServiceProtocol,
) -> None:
    """Re-check the bot after the write, and take the row back if it is gone.

    The existence check and the write are separate steps, so a deletion can run
    entirely between them: the deletion's pre-delete purge removes whatever is
    there, then this write puts a row back for a bot that no longer exists. A
    purge cannot cancel a request that already passed its check.

    Both orders are covered by the pair, which is the whole argument:

    * this write lands **before** the purge — the purge removes it;
    * the purge ran **first** — the bot reads as gone right here, and we remove
      our own row.

    What survives otherwise is not executable: ``uk_bot_id_entity_id_env_tenant``
    means no later bot can hold this key, so nothing will ever read the orphan.
    It is the caller's script text outliving the bot they deleted, which is the
    same residue ``_purge_startup_script`` propagates failures to avoid — a
    deletion path that refuses to report success over a surviving row should not
    leave one by a side door.

    **Unconditional, and that is new.** This withdrawal used to need the row's
    owner stamp, because between deciding to withdraw and issuing the delete the
    identifier could be recreated and the newcomer's own script destroyed. It
    cannot now: the key names one bot for the life of the data, so the only row
    that can be at it is ours.

    Losing the race is a ``404``. The bot the caller addressed is gone, and
    answering 200 would report a stored script for it.
    """
    try:
        if bot_service.get_bot(bot_id, owner_id) is not None:
            return
    except BotNotFoundError:
        pass

    # Propagates rather than being logged and swallowed: nothing else is coming
    # for this row once the deletion has finished, so a withdrawal we could not
    # complete must not be dressed up as the tidy 404 a clean one produces.
    startup_script_service.delete(entity_id=entity_id, bot_id=bot_id)
    raise BotNotFoundError(f"Bot not found: {bot_id}")


def _startup_script_payload(
    bot_id: str,
    record: Any,
    state: str,
    reason: str,
    manifest_body: Optional[str] = None,
    manifest_record: Any = None,
) -> StartupScript:
    """Shape a stored record — or its absence — as the response model.

    ``record`` is the startup-script row (or ``None``). On a bot with a
    manifest the alias view answers with the manifest instead (W8, §2.2):
    ``manifest_body`` is the manifest's own ``script.body`` and
    ``manifest_record`` the manifest row, whose modifier and timestamp become
    the audit fields — so the body is never stamped with the author of an
    older script row. Without a manifest both are ``None`` and the script
    row answers, as before W8.
    """
    from_manifest = manifest_body is not None
    script = manifest_body if from_manifest else (record.script if record is not None else "")
    audit = manifest_record if from_manifest and manifest_record is not None else record
    return StartupScript(
        bot_id=bot_id,
        script=script,
        size_bytes=len(script.encode("utf-8")),
        updated_by=audit.modifier if audit is not None else "",
        updated_at=audit.gmt_modified if audit is not None else None,
        supported=state == SUPPORTED,
        unsupported_reason=reason,
    )
