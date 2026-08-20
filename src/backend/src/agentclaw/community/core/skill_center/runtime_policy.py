"""Shared runtime eligibility policy for Skill and SkillSet commands."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.skill_center.errors import (
    SkillEngineNotSupportedError,
)


_SUPPORTED_BOT_SKILL_RUNTIMES = {
    "personal": frozenset({"openclaw", "claude_code", "hermes", "teclaw"}),
    "desktop": frozenset({"openclaw", "hermes"}),
    "service": frozenset({"openclaw", "claude_code", "teclaw"}),
}

# AICoding images implement the Claude Code logical Skill contract.  Runtime
# probes/adapters, not a second product matrix, decide their physical layout.
_LOGICAL_ENGINE_ALIASES = {"aicoding": "claude_code"}


def require_supported_bot_skill_runtime(bot: dict[str, Any]) -> None:
    """Fail closed outside the product-approved Bot × Engine matrix."""
    bot_type = str(bot.get("bot_type") or "")
    engine = _LOGICAL_ENGINE_ALIASES.get(
        str(bot.get("active_engine") or ""), str(bot.get("active_engine") or "")
    )
    if engine not in _SUPPORTED_BOT_SKILL_RUNTIMES.get(bot_type, frozenset()):
        raise SkillEngineNotSupportedError()


__all__ = ["require_supported_bot_skill_runtime"]
