"""Skills Pool 激活闭环使用的领域值对象。"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class MappingPublishOutcome:
    """One publish attempt and whether the runtime ended up converged.

    ``published`` and ``verified`` stay separate because the callers that
    report ``PUBLISH_FAILED`` and ``VERIFY_FAILED`` as distinct outcomes need
    to tell them apart; a caller that only cares whether the runtime converged
    reads ``verified`` alone (it is never ``True`` without ``published``).

    ``reported_inline`` says the publish response carried the verdict, so no
    separate verify round trip was needed — whichever way that verdict went.
    It is not "verified, inline": a runtime reporting ``verified: false``
    still reported inline. Evidence for logs and rollout metrics, never
    control flow; a caller deciding on it would be deciding on the device's
    build rather than on whether the mapping set converged.
    """

    published: bool
    verified: bool
    reported_inline: bool = False


__all__ = [
    "AICodingPoolPaths",
    "FILESYSTEM_POOL_ENGINES",
    "ClaudeCodePoolPaths",
    "HermesPoolPaths",
    "MappingPublishOutcome",
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
