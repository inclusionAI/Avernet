"""Scope helpers for render-screen CDN permissions."""
from __future__ import annotations

from typing import Any, Literal, Mapping

from agentclaw.community.core.bot_collaborator.services.aicoding.member_management_capability import (
    AICodingMemberManagementCapability,
)
from agentclaw.community.core.bot_collaborator.services.member_management_capability import (
    MemberManagementCapabilityService,
)

RenderScreenScope = Literal["owner", "bot"]


def _is_member_managed_coding_bot(bot: Mapping[str, Any]) -> bool:
    # Construct coordinator dynamically on demand to comply with the architecture
    # rule forbidding module-level service instances.
    capability_service = MemberManagementCapabilityService(
        engine_capabilities=(AICodingMemberManagementCapability(),),
    )
    return capability_service.uses_member_management_semantics(bot)


def resolve_render_screen_scope(bot: Mapping[str, Any] | None) -> RenderScreenScope:
    """Resolve whether a bot's render-screen CDN is owner-scoped or shared."""
    if not bot:
        return "owner"
    if _is_member_managed_coding_bot(bot):
        return "bot"
    return "owner"


def can_manage_render_screen_for_bot(
    bot: Mapping[str, Any] | None,
    *,
    user_id: str,
    collaborator_exists: bool,
) -> bool:
    """Return True when the user can manage the bot's render-screen records."""
    if not bot:
        return False
    owner_id = bot.get("owner_id")
    if owner_id and str(owner_id) == user_id:
        return True
    return collaborator_exists
