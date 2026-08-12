"""Startup-script helpers for the ``/openapi/v1/bots`` group (issue #926).

The three ``{bot_id}/startup-script`` handlers stay in ``router.py`` with the
rest of the group; the logic they share lives here. Split out when the router
crossed the 1000-line module cap — this is the part of it that is one cohesive
concern, and it is the part with reasoning worth reading on its own: what a
"bot incarnation" is, and why a write re-checks one after storing.

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
from agentclaw.community.core.bot_startup_script.errors import (
    StartupScriptSupersededError,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
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


def _bot_incarnation(bot: dict[str, Any]) -> int:
    """``ac_bots.id`` — which *existence* of this bot id we are talking about.

    ``bot_id`` alone does not identify a bot over time: deletion is a soft
    update and ``create_bot`` accepts a caller-supplied id, so the same id can
    later name a different bot. The primary key does not repeat, so it is what
    a stored script is pinned to.

    Absent is an error, not a default. The column is a NOT NULL autoincrement
    primary key that ``to_dict`` always carries, so this is a guard rather than
    a branch — but substituting a placeholder would silently attach a script to
    the wrong incarnation or hide one from the right one.
    """
    incarnation = bot.get("id")
    if incarnation is None:
        raise BotNotFoundError("bot record has no id")
    return int(incarnation)


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


def _abandon_write_if_the_bot_died(
    bot_id: str,
    entity_id: str,
    owner_id: str,
    bot_incarnation: int,
    bot_service: BotServiceProtocol,
    startup_script_service: BotStartupScriptServiceProtocol,
) -> None:
    """Re-check *the same bot* after the write, and undo the write if it is gone.

    The existence check above and the write are separate steps, so a deletion
    can run entirely between them. The deletion's own two purges do not cover
    this on their own: they stop a *later* request from passing its check, but
    they cannot cancel a request that already passed one and is still in
    flight. That request can commit at any point afterwards, past both sweeps.

    Re-checking after the write is what closes it, and the ordering is the
    whole argument:

    * a deleter that commits **after** our write finds our row in its
      post-delete sweep and removes it;
    * a deleter that committed **before** this check is seen right here, and we
      withdraw our own row.

    It costs one read on the write path instead of a lock held across every
    script write — the same trade ``_sweep_grants_that_raced_the_deletion``
    documents for the equivalent grant race.

    **By incarnation, not by identifier.** "A bot with this id exists" is not
    the check worth making: ``create_bot`` accepts a caller-supplied ``bot_id``
    and deletion is a soft update, so the id can be deleted and handed to a new
    bot while this request is in flight, and a bare existence check would pass
    against that stranger — attaching our caller's executable content to a bot
    whose owner never asked for it. ``ac_bots.id`` is an autoincrement primary
    key, so the recreated bot carries a different one and the substitution is
    visible.

    Losing the race is a ``404``, not a success: the bot the caller addressed no
    longer exists, and reporting a stored script for it would be the same silent
    wrong answer this feature keeps closing elsewhere.
    """
    try:
        current = bot_service.get_bot(bot_id, owner_id)
    except BotNotFoundError:
        current = None

    if current is not None and current.get("id") == bot_incarnation:
        return

    # Conditional on our own incarnation: between the check above and this
    # delete, the identifier can be recreated and the new bot can store a
    # script of its own at this key. An unconditional delete would destroy it,
    # making one caller's lost race another caller's lost script.
    #
    # The failure propagates rather than being logged and swallowed. The
    # deletion's second sweep is only a backstop while that deleter is still
    # running; when it has already finished — which is precisely the case where
    # our write landed after both sweeps — nothing else will ever come for this
    # row. Answering 404 then would report the write undone while leaving
    # executable content behind under a reusable identifier, so a withdrawal we
    # could not complete has to be visible as a failure.
    startup_script_service.delete_written_by(
        entity_id=entity_id, bot_id=bot_id, bot_incarnation=bot_incarnation
    )
    raise BotNotFoundError(f"Bot not found: {bot_id}")


def _store_or_404_if_superseded(
    startup_script_service: BotStartupScriptServiceProtocol,
    *,
    entity_id: str,
    bot_id: str,
    script: str,
    bot_incarnation: int,
    modifier: str,
):
    """Store the script, answering 404 when a later bot already owns the key.

    The store refuses a write whose bot is older than the row's current owner:
    the writer's bot was deleted and its identifier handed on mid-request, and
    overwriting would destroy the new owner's script.

    That is the same situation as the bot simply being gone, and it gets the
    same answer. The caller did nothing wrong — they addressed a bot that
    stopped existing while they were talking to it — so a 404 on that bot is
    both true and the outcome they can act on, where a 500 would suggest a
    fault on our side and invite a retry that will never succeed.
    """
    try:
        return startup_script_service.put(
            entity_id=entity_id,
            bot_id=bot_id,
            script=script,
            bot_incarnation=bot_incarnation,
            modifier=modifier,
        )
    except StartupScriptSupersededError as exc:
        raise BotNotFoundError(f"Bot not found: {bot_id}") from exc
