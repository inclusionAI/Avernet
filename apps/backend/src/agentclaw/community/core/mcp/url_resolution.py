"""Resolve an MCP's explicit URL independently of its Header rules."""

from __future__ import annotations

from typing import Any


def effective_mcp_url_override(
    user_defaults: dict[str, Any] | None,
    bot_config: dict[str, Any] | None,
) -> str | None:
    """Bot URL wins; absent Bot URL inherits the user's global URL."""
    if bot_config is not None and "url" in bot_config:
        return bot_config["url"]
    if not isinstance(user_defaults, dict):
        return None
    return user_defaults.get("url")
