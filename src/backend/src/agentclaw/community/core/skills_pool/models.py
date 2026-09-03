"""Skills Pool 激活闭环使用的领域值对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agentclaw.community.core.workspace.skill_layout import (
    AICodingPoolPaths,
    ClaudeCodePoolPaths,
    FILESYSTEM_POOL_ENGINES,
    HermesPoolPaths,
    OpenClawPoolPaths,
    PoolPaths,
    pool_paths_for_engine,
)


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
    skill_uuid: str | None = None
    sc_version_number: str | None = None
    mcp_dependencies: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class PoolSkillMapping:
    """Backend-owned logical intent; Engine resolves filesystem paths."""

    corpus: str
    relative_path: str | None
    link_name: str
    skill_uuid: str | None = None
    sc_version_number: str | None = None

    def to_dict(self) -> dict[str, str]:
        result = {
            "corpus": self.corpus,
            "link_name": self.link_name,
        }
        if self.corpus == "center":
            if self.skill_uuid is None or self.sc_version_number is None:
                raise ValueError("center mapping requires structured identity")
            return {
                **result,
                "skill_uuid": self.skill_uuid,
                "sc_version_number": self.sc_version_number,
            }
        if self.relative_path is None:
            raise ValueError("mapping requires relative_path")
        return {**result, "relative_path": self.relative_path}


class SkillMappingSourceLayout(StrEnum):
    """运行时 mapping source 所属的权威 layout。"""

    POOL = "pool"
    LEGACY = "legacy"


class MappingApplyMode(StrEnum):
    """The Engine handling policy for one full Mapping request."""

    STRICT = "STRICT"
    BEST_EFFORT = "BEST_EFFORT"


class MappingProjectionStatus(StrEnum):
    CONVERGED = "CONVERGED"
    PENDING = "PENDING"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class MappingItemResult:
    """Engine evidence for one logical mapping target.

    Paths remain infrastructure evidence at this seam.  The Runtime Projector
    translates them to user-safe Skill details before any HTTP adapter sees
    them.
    """

    target: str
    source: str | None
    status: MappingProjectionStatus
    code: str | None = None
    retryable: bool = False
    action: str = "APPLY"


@dataclass(frozen=True, slots=True)
class MappingPublishResult:
    published: bool
    status: MappingProjectionStatus
    items: tuple[MappingItemResult, ...] = ()
    evidence: dict[str, object] = field(default_factory=dict)

    def __bool__(self) -> bool:
        """Keep existing strict Pool callers source-compatible."""

        return self.published


@dataclass(frozen=True, slots=True)
class MappingVerificationResult:
    valid: bool
    status: MappingProjectionStatus
    items: tuple[MappingItemResult, ...] = ()
    evidence: dict[str, object] = field(default_factory=dict)

    def __bool__(self) -> bool:
        """Keep existing strict Pool callers source-compatible."""

        return self.valid


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
    "MappingApplyMode",
    "MappingItemResult",
    "MappingProjectionStatus",
    "MappingPublishResult",
    "MappingVerificationResult",
    "PoolSkillMapping",
    "RegisteredSkillAsset",
    "SkillMappingSourceLayout",
    "local_locator_prefixes",
    "pool_paths_for_engine",
]
