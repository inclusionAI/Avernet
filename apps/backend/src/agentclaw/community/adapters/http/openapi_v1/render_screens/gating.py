"""Authorization and record scoping for public render-screen operations."""

from __future__ import annotations

from typing import Any, Mapping

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.render_screen_service import RenderScreenServiceProtocol
from agentclaw.community.core.bot_management.render_screen.errors import (
    RenderScreenNotFoundError,
)
from agentclaw.community.core.bot_management.render_screen.models import (
    RenderScreenRecord,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.utils.env_utils import get_current_env


def resolve_readable_bot(
    bots: BotServiceProtocol,
    *,
    bot_id: str,
    owner_id: str,
) -> Mapping[str, Any]:
    """Resolve a Bot for non-sensitive render-screen reads.

    Authentication and, for an application caller, its Bot grant are enforced
    by route dependencies. Human reads deliberately do not require an Editor
    relation because group/share viewers need the CDN mapping to render panels.

    **Called by the read alone, and that is the whole point.** ``GET`` is the
    only operation in this group whose row is ``NoCheck``, so it is the only one
    the seam does not resolve for — which makes this the sole proof that the
    addressed Bot exists under the named owner. The three mutations carry
    ``Check(MEMBER)``: ``bot_access._level`` has already run
    ``get_by_id_and_owner`` and refused on absence before their handlers are
    entered, so calling this there would re-read the Bot to learn what the seam
    just proved. If a mutation ever moves off ``Check``, it needs this back.
    """
    try:
        bot = bots.get_bot(bot_id, owner_id)
    except BotNotFoundError:
        raise RenderScreenNotFoundError("render screen not found") from None
    if not bot:
        raise RenderScreenNotFoundError("render screen not found")
    return bot


def require_scoped_record(
    service: RenderScreenServiceProtocol,
    *,
    record_id: int,
    bot_id: str,
    owner_id: str,
    actor_id: str,
) -> RenderScreenRecord:
    """Bind a public record id back to the addressed Bot before mutation."""
    try:
        record = service.authorize_render_screen_record(record_id=record_id, user_id=actor_id)
    except (PermissionError, ValueError):
        raise RenderScreenNotFoundError("render screen not found") from None
    # COSEC: a numeric render-screen id is never authority. Rebind it to the
    # addressed Bot and environment so guessed ids cannot cross scopes.
    if (
        record is None
        or record.bot_id != bot_id
        or record.env != get_current_env()
    ):
        raise RenderScreenNotFoundError("render screen not found")
    return record


__all__ = [
    "require_scoped_record",
    "resolve_readable_bot",
]
