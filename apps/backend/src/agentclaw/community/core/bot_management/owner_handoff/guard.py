"""Shared maintenance check for existing Bot mutation boundaries."""

import json
from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.bot_management.owner_handoff.contracts import HandoffError


def assert_bot_mutable(bot: Mapping[str, Any]) -> None:
    ext = bot.get("ext") or {}
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except json.JSONDecodeError:
            # Legacy editor policy already tolerates malformed historical ext.
            # Such a value cannot contain a valid journal marker; handoff begin
            # separately requires valid JSON before writing any intent.
            return
    if isinstance(ext, Mapping) and ext.get("owner_handoff"):
        raise HandoffError("BOT_OWNER_HANDOFF_IN_PROGRESS")
