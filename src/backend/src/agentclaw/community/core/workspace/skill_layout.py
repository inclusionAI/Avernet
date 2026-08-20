"""Pure filesystem-engine Skill layout compatibility contract.

The Engine planner remains authoritative for runtime layout selection.  Backend
consumers use these values only to address the selected Legacy/Pool roots at
the device-filesystem seam and to validate Engine-returned evidence.  Keeping
the value objects in this dependency-free workspace module lets low-level path
code consume them without importing Skills Pool orchestration or initializing
the DI composition root.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agentclaw.community.core.workspace.runtime_identity import (
    claude_code_uses_aicoding_runtime,
)


@dataclass(frozen=True, slots=True)
class OpenClawPoolPaths:
    active: str = "/home/admin/.openclaw/workspace/skills"
    legacy_local: str = "/home/admin/.openclaw/workspace/skills/skills-local"
    legacy_repo: str = "/home/admin/.openclaw/workspace/skills/skills-repo"
    pool_local: str = "/home/admin/.openclaw/workspace/skills-pool/skills-local"
    pool_repo: str = "/home/admin/.openclaw/workspace/skills-pool/skills-repo"


@dataclass(frozen=True, slots=True)
class ClaudeCodePoolPaths:
    active: str = "/home/admin/.claude/skills"
    legacy_local: str = "/home/admin/.claude_code/workspace/skills/skills-local"
    legacy_repo: str = "/home/admin/.claude_code/skills-repo"
    pool_local: str = "/home/admin/.claude_code/workspace/skills-pool/skills-local"
    pool_repo: str = "/home/admin/.claude_code/workspace/skills-pool/skills-repo"


@dataclass(frozen=True, slots=True)
class AICodingPoolPaths:
    active: str = "/home/admin/.claude/skills"
    legacy_local: str = "/home/admin/.aicoding/workspace/skills/skills-local"
    legacy_repo: str = "/home/admin/.aicoding/skills-repo"
    pool_local: str = "/home/admin/.aicoding/workspace/skills-pool/skills-local"
    pool_repo: str = "/home/admin/.aicoding/workspace/skills-pool/skills-repo"


@dataclass(frozen=True, slots=True)
class HermesPoolPaths:
    active: str = "/home/admin/.hermes/skills"
    legacy_local: str = "/home/admin/.hermes/workspace/skills/skills-local"
    legacy_repo: str = "/home/admin/.hermes/skills-repo"
    pool_local: str = "/home/admin/.hermes/workspace/skills-pool/skills-local"
    pool_repo: str = "/home/admin/.hermes/workspace/skills-pool/skills-repo"


PoolPaths = (
    OpenClawPoolPaths | ClaudeCodePoolPaths | AICodingPoolPaths | HermesPoolPaths
)
FILESYSTEM_POOL_ENGINES = ("openclaw", "claude_code", "aicoding", "hermes")

_CLAUDE_CODE_AICODING_TEMPLATES = frozenset(
    {"personalCoding", "applicationCoding"}
)


def runtime_layout_engine_for_bot(bot: Mapping[str, object]) -> str:
    """Return the filesystem identity for a Bot's Skill runtime.

    ``claude_code`` remains the logical product engine for coding templates:
    catalogue selection, Passport, and persisted control-plane state therefore
    continue to use it.  Those templates run in an AICoding image, however,
    so every filesystem Pool operation must address AICoding's physical roots.

    This dependency-free workspace contract is deliberately shared by Skill
    Center and Skills Pool.  It prevents either domain from inferring a
    physical path from ``active_engine`` alone.
    """

    engine = str(bot.get("active_engine") or "")
    if (
        engine == "claude_code"
        and str(bot.get("template_type") or "")
        in _CLAUDE_CODE_AICODING_TEMPLATES
    ):
        return "aicoding"
    return engine


def runtime_layout_engine_for_bot(bot: Mapping[str, object]) -> str:
    """Return the filesystem identity for a Bot's Skill runtime.

    ``claude_code`` remains the logical product engine for coding templates:
    catalogue selection, Passport, and persisted control-plane state therefore
    continue to use it.  Those templates run in an AICoding image, however,
    so every filesystem Pool operation must address AICoding's physical roots.

    This dependency-free workspace contract is deliberately shared by Skill
    Center and Skills Pool.  It prevents either domain from inferring a
    physical path from ``active_engine`` alone.
    """

    engine = str(bot.get("active_engine") or "")
    template_type = str(bot.get("template_type") or "")
    if claude_code_uses_aicoding_runtime(
        active_engine=engine,
        template_type=template_type,
    ):
        return "aicoding"
    return engine


def pool_paths_for_engine(engine: str) -> PoolPaths:
    """Resolve explicitly supported filesystem engines; never fall back."""

    if engine == "openclaw":
        return OpenClawPoolPaths()
    if engine == "claude_code":
        return ClaudeCodePoolPaths()
    if engine == "aicoding":
        return AICodingPoolPaths()
    if engine == "hermes":
        return HermesPoolPaths()
    raise ValueError(f"engine Pool layout not implemented: {engine}")


__all__ = [
    "AICodingPoolPaths",
    "ClaudeCodePoolPaths",
    "FILESYSTEM_POOL_ENGINES",
    "HermesPoolPaths",
    "OpenClawPoolPaths",
    "PoolPaths",
    "pool_paths_for_engine",
    "runtime_layout_engine_for_bot",
]
