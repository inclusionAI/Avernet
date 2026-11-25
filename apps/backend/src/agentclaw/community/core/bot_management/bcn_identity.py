"""TeamClaw BCN references, separate from mutable BCN administration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.bot_management.addressing import stable_resource_owner_id


def bcn_bot_reference(bot: Mapping[str, Any]) -> str:
    """Resolve the established TeamClaw composite identity from a Bot row.

    This is the TeamClaw registration/addressing contract, not a conversion of
    arbitrary Providers' references into BCN UUIDs. Existing qualified external
    IDs are handled by their consumers without reinterpretation.
    """
    if bot.get("bot_type") != "service" or bot["bot_id"] == "default":
        owner_id = bot.get("owner_id")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("Bot owner_id is required for legacy BCN addressing")
        return f"{bot['bot_id']}:{owner_id}"
    # Personal-to-service conversion preserves its original switched binding.
    ext = bot.get("ext")
    existing = ext.get("bcn_provider_bot_ref") if isinstance(ext, Mapping) else None
    if existing is not None:
        prefix = f"{bot['bot_id']}:"
        if (
            not isinstance(existing, str)
            or not existing.startswith(prefix)
            or not existing[len(prefix):].strip()
        ):
            raise ValueError("Invalid stored BCN provider reference")
        return existing
    return f"{bot['bot_id']}:{stable_resource_owner_id(bot)}"


def provider_address_kwargs(bot: Mapping[str, Any]) -> dict[str, str]:
    """Supply the shared reference while preserving old non-service signatures."""
    if bot.get("bot_type") != "service" or bot["bot_id"] == "default":
        return {}
    return {"provider_bot_ref": bcn_bot_reference(bot)}
