"""Skills Pool 激活闭环使用的领域值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class OpenClawPoolPaths:
    """OpenClaw P3 的容器视角路径契约。"""

    active: str = "/home/admin/.openclaw/workspace/skills"
    legacy_local: str = "/home/admin/.openclaw/workspace/skills/skills-local"
    legacy_repo: str = "/home/admin/.openclaw/workspace/skills/skills-repo"
    pool_local: str = "/home/admin/.openclaw/workspace/skills-pool/skills-local"
    pool_repo: str = "/home/admin/.openclaw/workspace/skills-pool/skills-repo"


@dataclass(frozen=True, slots=True)
class ClaudeCodePoolPaths:
    """Claude Code P3 的容器视角路径契约。"""

    active: str = "/home/admin/.claude/skills"
    legacy_local: str = "/home/admin/.claude_code/workspace/skills/skills-local"
    legacy_repo: str = "/home/admin/.claude_code/skills-repo"
    pool_local: str = "/home/admin/.claude_code/workspace/skills-pool/skills-local"
    pool_repo: str = "/home/admin/.claude_code/workspace/skills-pool/skills-repo"


@dataclass(frozen=True, slots=True)
class AICodingPoolPaths:
    """AICoding P3 的容器视角路径契约。"""

    active: str = "/home/admin/.claude/skills"
    legacy_local: str = "/home/admin/.aicoding/workspace/skills/skills-local"
    legacy_repo: str = "/home/admin/.aicoding/skills-repo"
    pool_local: str = "/home/admin/.aicoding/workspace/skills-pool/skills-local"
    pool_repo: str = "/home/admin/.aicoding/workspace/skills-pool/skills-repo"


@dataclass(frozen=True, slots=True)
class HermesPoolPaths:
    """Hermes P3 的容器视角路径契约。"""

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
    """Resolve only explicitly supported engine layouts; never fall back."""

    if engine == "openclaw":
        return OpenClawPoolPaths()
    if engine == "claude_code":
        return ClaudeCodePoolPaths()
    if engine == "aicoding":
        return AICodingPoolPaths()
    if engine == "hermes":
        return HermesPoolPaths()
    raise ValueError(f"engine Pool layout not implemented: {engine}")


def local_locator_prefixes(*, pool: bool) -> tuple[str, ...]:
    """Return every supported engine's canonical local locator prefix."""

    attribute = "pool_local" if pool else "legacy_local"
    return tuple(
        f"local://{getattr(pool_paths_for_engine(engine), attribute)}/"
        for engine in FILESYSTEM_POOL_ENGINES
    )


@dataclass(frozen=True, slots=True)
class RegisteredSkillAsset:
    """Backend 中属于一个 Bot 的技能来源记录。"""

    skill_id: int
    name: str
    git_path: str


@dataclass(frozen=True, slots=True)
class PoolSkillMapping:
    """显式以声明 source layout 构造的运行时 mapping。"""

    source: str
    target: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "target": self.target}


class SkillMappingSourceLayout(StrEnum):
    """运行时 mapping source 所属的权威 layout。"""

    POOL = "pool"
    LEGACY = "legacy"


class PoolCutoverStatus(StrEnum):
    """Backend 与 Engine 激活端点之间的稳定状态契约。"""

    COMMITTED = "COMMITTED"
    ALREADY_COMMITTED = "ALREADY_COMMITTED"
    ACTIVE_ENTRY_CONFLICT = "ACTIVE_ENTRY_CONFLICT"
    DATA_INCONSISTENT = "DATA_INCONSISTENT"
    INVALID = "INVALID"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    POST_CUTOVER_SYNC_PENDING = "POST_CUTOVER_SYNC_PENDING"
    NOT_ATOMIC = "NOT_ATOMIC"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PoolCutoverResult:
    """Engine 激活响应在 Backend 领域层的类型化表示。"""

    committed: bool
    status: PoolCutoverStatus
    evidence: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "committed": self.committed,
            "status": self.status.value,
            "evidence": self.evidence,
        }


__all__ = [
    "AICodingPoolPaths",
    "FILESYSTEM_POOL_ENGINES",
    "ClaudeCodePoolPaths",
    "HermesPoolPaths",
    "OpenClawPoolPaths",
    "PoolPaths",
    "PoolCutoverResult",
    "PoolCutoverStatus",
    "PoolSkillMapping",
    "RegisteredSkillAsset",
    "SkillMappingSourceLayout",
    "local_locator_prefixes",
    "pool_paths_for_engine",
]
