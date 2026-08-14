"""Pure filesystem-engine Skill layout compatibility contract.

The Engine planner remains authoritative for runtime layout selection.  Backend
consumers use these values only to address the selected Legacy/Pool roots at
the device-filesystem seam and to validate Engine-returned evidence.  Keeping
the value objects in this dependency-free workspace module lets low-level path
code consume them without importing Skills Pool orchestration or initializing
the DI composition root.
"""

from __future__ import annotations

from dataclasses import dataclass


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
]
