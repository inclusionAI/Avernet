"""Shared runtime eligibility policy for Skill and SkillSet commands."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from agentclaw.community.core.skill_center.errors import (
    SkillEngineNotSupportedError,
)
from agentclaw.community.core.workspace.skill_layout import (
    runtime_layout_engine_for_bot,
)


_SUPPORTED_BOT_SKILL_RUNTIMES = {
    "personal": frozenset({"openclaw", "claude_code", "hermes", "teclaw"}),
    "desktop": frozenset({"openclaw", "hermes"}),
    "service": frozenset({"openclaw", "claude_code", "teclaw"}),
}

_CLAUDE_CODE_WRITABLE_TEMPLATES = frozenset(
    {"personalCoding", "applicationCoding"}
)


class BotSkillRuntimeMutationMode(StrEnum):
    """The mutation authority available to a persisted Bot runtime.

    ``FULL`` is the product-supported matrix and may create or activate
    capability state.  ``CLEANUP_ONLY`` deliberately exists for historical
    records that can still safely *remove* existing Local/Repo/MCP state but
    cannot receive a new Pool/Center projection.  A named mode is important:
    callers must never turn this safety boundary into an ambiguous boolean.
    """

    FULL = "FULL"
    CLEANUP_ONLY = "CLEANUP_ONLY"


class BotSkillRuntimeCommand(StrEnum):
    """Explicit command intent at a Bot capability mutation boundary."""

    WRITE = "WRITE"
    CLEANUP = "CLEANUP"


def bot_skill_runtime_mutation_mode(
    bot: dict[str, Any],
) -> BotSkillRuntimeMutationMode:
    """Classify the intentional mutation authority of a Bot record.

    Historical plain Claude Code, literal AICoding, and Desktop Claude Code
    records are not eligible for new capability writes.  They remain able to
    reconcile an already-reduced legacy Local/Repo/MCP projection while users
    remove old state.  Everything else outside the declared matrix stays
    fail-closed.
    """

    # Historical Local rows predate a durable ``bot_type`` column.  Their
    # released behaviour was personal-Bot semantics, so retain that narrow
    # compatibility default instead of rejecting every old Local operation.
    bot_type = str(bot.get("bot_type") or "personal")
    engine = str(bot.get("active_engine") or "")
    template_type = str(bot.get("template_type") or "")

    if engine in _SUPPORTED_BOT_SKILL_RUNTIMES.get(bot_type, frozenset()):
        if engine != "claude_code" or template_type in _CLAUDE_CODE_WRITABLE_TEMPLATES:
            return BotSkillRuntimeMutationMode.FULL

    if engine == "aicoding" or (engine == "claude_code" and bot_type == "desktop"):
        return BotSkillRuntimeMutationMode.CLEANUP_ONLY
    if engine == "claude_code" and bot_type in {"personal", "service"}:
        return BotSkillRuntimeMutationMode.CLEANUP_ONLY
    raise SkillEngineNotSupportedError()


def require_supported_bot_skill_runtime(bot: dict[str, Any]) -> None:
    """Fail closed outside the product-approved Bot × Engine matrix."""
    if bot_skill_runtime_mutation_mode(bot) is not BotSkillRuntimeMutationMode.FULL:
        raise SkillEngineNotSupportedError()


def require_cleanup_capable_bot_skill_runtime(
    bot: dict[str, Any],
) -> BotSkillRuntimeMutationMode:
    """Allow the explicit safe-cleanup subset without granting full writes."""

    return bot_skill_runtime_mutation_mode(bot)


def require_bot_skill_runtime_command(
    bot: dict[str, Any],
    command: BotSkillRuntimeCommand,
) -> BotSkillRuntimeMutationMode:
    """Authorize a named command without widening a cleanup-only record."""

    mode = bot_skill_runtime_mutation_mode(bot)
    if command is BotSkillRuntimeCommand.WRITE:
        require_supported_bot_skill_runtime(bot)
    return mode


__all__ = [
    "BotSkillRuntimeMutationMode",
    "BotSkillRuntimeCommand",
    "bot_skill_runtime_mutation_mode",
    "require_bot_skill_runtime_command",
    "require_cleanup_capable_bot_skill_runtime",
    "require_supported_bot_skill_runtime",
    "runtime_layout_engine_for_bot",
]
