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

_CLAUDE_CODE_WRITABLE_TEMPLATES = frozenset(
    {"personalCoding", "applicationCoding"}
)


def require_supported_bot_skill_runtime(bot: dict[str, Any]) -> None:
    """Fail closed outside the product-approved Bot × Engine matrix."""
    bot_type = str(bot.get("bot_type") or "")
    # New AICoding-image Bots still expose the logical ``claude_code`` engine
    # (their template_type selects the physical image). Historical rows whose
    # active_engine is literally ``aicoding`` retain safe read/delete only and
    # must not silently gain new mutation/runtime support.
    engine = str(bot.get("active_engine") or "")
    if engine not in _SUPPORTED_BOT_SKILL_RUNTIMES.get(bot_type, frozenset()):
        raise SkillEngineNotSupportedError()
    if (
        engine == "claude_code"
        and str(bot.get("template_type") or "")
        not in _CLAUDE_CODE_WRITABLE_TEMPLATES
    ):
        raise SkillEngineNotSupportedError()


__all__ = ["require_supported_bot_skill_runtime"]
