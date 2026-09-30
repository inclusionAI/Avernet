"""Scope helpers for render-screen CDN permissions."""
from __future__ import annotations

from typing import Any, Literal, Mapping

RenderScreenScope = Literal["owner", "bot"]

# Template types that represent plain Claude Code bots (non-collaborative).
# Empty string and None are included because some legacy bots have no template_type.
_NORMAL_CC_TEMPLATE_TYPES = frozenset({"normal", "normalCC", "", None})


def _is_agent_coding_bot(bot: Mapping[str, Any]) -> bool:
    """Return True when the bot is an Agent Coding Bot (collaborative).

    Rule: active_engine == "claude_code" AND template_type is not a plain CC type.
    Plain CC types are: empty/None, "normal", "normalCC".
    Everything else on claude_code is an Agent Coding Bot.
    """
    active_engine = bot.get("active_engine")
    template_type = bot.get("template_type")
    return (
        active_engine == "claude_code"
        and template_type not in _NORMAL_CC_TEMPLATE_TYPES
    )


def resolve_render_screen_scope(bot: Mapping[str, Any] | None) -> RenderScreenScope:
    """Resolve whether a bot's render-screen CDN is owner-scoped or shared."""
    if not bot:
        return "owner"
    if _is_agent_coding_bot(bot):
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
