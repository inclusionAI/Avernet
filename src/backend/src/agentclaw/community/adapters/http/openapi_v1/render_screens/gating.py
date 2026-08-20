"""Authorization and record scoping for public render-screen operations."""

from __future__ import annotations

from typing import Any, Mapping

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.api.render_screen_service import RenderScreenServiceProtocol
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
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
    """
    try:
        bot = bots.get_bot(bot_id, owner_id)
    except BotNotFoundError:
        raise RenderScreenNotFoundError("render screen not found") from None
    if not bot:
        raise RenderScreenNotFoundError("render screen not found")
    return bot


def require_editable_bot(
    bots: BotServiceProtocol,
    collaborators: CollaboratorServiceProtocol,
    *,
    bot_id: str,
    owner_id: str,
    actor_id: str,
) -> Mapping[str, Any]:
    """Resolve the Bot and require its live effective Editor permission."""
    bot = resolve_readable_bot(bots, bot_id=bot_id, owner_id=owner_id)
    level = collaborators.get_operable_permission_level(
        bot=bot,
        user_id=actor_id,
    )
    if level < PermissionLevel.MEMBER:
        # COSEC: mask edit authorization failures as absence to prevent Bot-ID
        # probing while still allowing authenticated viewers to use the GET.
        raise RenderScreenNotFoundError("render screen not found")
    return bot


def require_scoped_record(
    service: RenderScreenServiceProtocol,
    *,
    record_id: int,
    bot_id: str,
    owner_id: str,
) -> RenderScreenRecord:
    """Bind a public record id back to the addressed Bot before mutation."""
    record = service.get_render_screen(record_id)
    # COSEC: a numeric render-screen id is never authority. Rebind it to the
    # addressed Bot, owner and environment so guessed ids cannot cross scopes.
    if (
        record is None
        or record.bot_id != bot_id
        or record.owner_id != owner_id
        or record.env != get_current_env()
    ):
        raise RenderScreenNotFoundError("render screen not found")
    return record


__all__ = [
    "require_editable_bot",
    "require_scoped_record",
    "resolve_readable_bot",
]
