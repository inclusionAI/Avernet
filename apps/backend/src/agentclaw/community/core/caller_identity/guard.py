"""The checks every owner-only change to a bot's CLIs runs first.

Shared by the CLI identity switch and the acli CLI API, so both refuse the
same requests with the same errors.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityAmbiguousError,
    CallerIdentityNotFoundError,
    CallerIdentityPermissionError,
    CallerIdentityReadOnlyError,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLockRepositoryProtocol,
    BotRepository,
)


def load_bot(
    bot_repository: BotRepository, *, bot_id: str, entity_id: str | None
) -> Mapping[str, Any]:
    """The bot, scoped to ``entity_id`` when given; never an arbitrary duplicate."""
    if entity_id is not None:
        bot = bot_repository.get_by_id_and_entity(bot_id, entity_id)
    else:
        try:
            # COSEC: do not select an arbitrary duplicate Bot when callers
            # omit entity_id; the caller-specific lookup fails closed.
            bot = bot_repository.get_unique_by_id(bot_id)
        except BotLookupAmbiguousError as exc:
            raise CallerIdentityAmbiguousError from exc
    if bot is None:
        raise CallerIdentityNotFoundError
    return bot


def require_owner_of_active_service_bot(bot: Mapping[str, Any], actor_id: str) -> None:
    """Only the owner may change an active service bot's caller identity.

    Caller identity exists only for service bots, which other people call. The
    bot is checked first (an inactive or non-service bot is read-only for
    everyone), then the actor (only its owner may change it).
    """
    if bot.get("bot_type") != "service" or bot.get("status") != "ACTIVE":
        raise CallerIdentityReadOnlyError
    # COSEC: entity-scoped lookup identifies the Bot; only its owner can mutate it.
    if str(bot.get("owner_id") or "") != actor_id:
        raise CallerIdentityPermissionError


def lock_epoch_matches(
    lock_repository: BotCollabLockRepositoryProtocol,
    *,
    bot_id: str,
    owner_id: str,
    actor_id: str,
    lock_epoch: int | None,
) -> bool:
    """Whether the caller's collaboration-lock epoch is current.

    The collaboration lock is one database row per bot, keyed
    ``{bot_id}:{entity_id}``; the legacy ``owner_id`` argument is the
    already-resolved entity locator (as ``CollaboratorLockService`` builds it) and held
    by one user at a time; ``lock_epoch`` is that row's id. No epoch is valid
    only while no lock exists; a supplied epoch must name the lock the actor
    holds, so a client acting on a lock it has since lost is refused.
    """
    lock = lock_repository.get_by_key(f"{bot_id}:{owner_id}")
    if lock_epoch is None:
        return lock is None
    return lock is not None and lock.holder_user_id == actor_id and lock.id == lock_epoch


__all__ = ["load_bot", "lock_epoch_matches", "require_owner_of_active_service_bot"]
