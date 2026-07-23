"""
Skills data models.

See src/engine/docs/heterogeneous-engine-architecture.md §7.2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any


class SkillType(Enum):
    """How a skill is installed / supplied."""

    SYMLINK = "symlink"     # symbolic link to a source directory (current default)
    BUILTIN = "builtin"     # shipped with the engine itself
    PACKAGE = "package"     # installed from a package registry
    CUSTOM = "custom"       # engine-specific mechanism


class SkillStatus(Enum):
    """Runtime status of a skill on the host."""

    INSTALLED = "installed"
    AVAILABLE = "available"
    ERROR = "error"
    DISABLED = "disabled"
    INSTALLING = "installing"


@dataclass
class SkillConfig:
    """Declared configuration for a skill.

    `source` and `target` interpretation depends on `skill_type`:
      - SYMLINK: source = source directory, target = link destination
      - PACKAGE: source = package name/url
      - BUILTIN / CUSTOM: engine-specific
    """

    skill_id: str
    skill_type: SkillType = SkillType.SYMLINK
    source: str | None = None
    target: str | None = None
    enabled: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class Skill:
    """A skill registered with the engine."""

    skill_id: str
    name: str
    description: str
    config: SkillConfig
    status: SkillStatus
    version: str | None = None
    dependencies: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)


@dataclass
class SkillExecutionRequest:
    """Request to execute a skill action."""

    skill_id: str
    action: str
    parameters: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillExecutionResult:
    """Outcome of a skill execution."""

    skill_id: str
    action: str
    success: bool
    output: Any
    error: str | None = None
    duration_ms: int = 0


@dataclass
class SymlinkItem:
    """A single source→target pair for the bulk symlink-management ops.

    Used by :meth:`SkillsService.sync_symlinks` (paths relative to a
    base dir) and :meth:`SkillsService.sync_bindpaths` (absolute paths).
    """

    source: str
    target: str


@dataclass
class SyncSymlinksRequest:
    """Bulk-sync request for relative-path symlinks under a base dir."""

    symlinks: list[SymlinkItem] = field(default_factory=list)


@dataclass
class SyncBindPathsRequest:
    """Bulk-sync request for absolute-path symlinks.

    `clean_target_dir` controls whether to remove pre-existing symlinks in
    each unique parent directory of `symlinks` that aren't in the request.
    """

    symlinks: list[SymlinkItem] = field(default_factory=list)
    clean_target_dir: bool = True


@dataclass
class SyncSymlinksResult:
    """Outcome of a bulk-sync operation.

    Engine implementations populate `base_dir` only for the symlink form
    (where everything sits under one root); the bindpath form leaves it
    `None`.
    """

    total: int
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    base_dir: str | None = None


@dataclass
class CleanSymlinksRequest:
    """Request to remove every symlink under each of `directories`."""

    directories: list[str] = field(default_factory=list)


@dataclass
class CleanSymlinksResult:
    """Outcome of a directory-scoped symlink cleanup."""

    directories_scanned: int
    removed: list[str] = field(default_factory=list)


@dataclass
class CenterEnsureItem:
    """One skill (uuid + version string) the engine should ensure is present locally."""
    skill_uuid: str
    version: str


@dataclass
class CenterEnsureFailure:
    """An item that ensure could not satisfy."""
    skill_uuid: str
    version: str
    reason: str


@dataclass
class CenterEnsureRequest:
    """Backend asks engine to ensure these skill versions exist locally."""
    items: list[CenterEnsureItem] = field(default_factory=list)


@dataclass
class CenterEnsureResult:
    """Outcome of ensure_center_skills."""
    ok: list[CenterEnsureItem] = field(default_factory=list)
    failed: list[CenterEnsureFailure] = field(default_factory=list)


@dataclass
class PoolLayoutActivateRequest:
    """提交 OpenClaw Pool 数据面所需的稳定 Service API 请求。

    ``registered_local_names`` 用于核对数据库登记事实；运行时还会枚举并
    迁移 Legacy local 和 active 入口中的完整文件系统事实。
    """

    migration_generation: str
    preparation_id: str
    registered_local_names: list[str] = field(default_factory=list)
    mappings: list[SymlinkItem] = field(default_factory=list)


@dataclass
class PoolLayoutRollbackRequest:
    """Explicit Pool→Legacy rollback from the current authoritative Pool."""

    rollback_generation: str
    registered_local_names: list[str] = field(default_factory=list)


@dataclass
class PoolLayoutProbeRequest:
    """运行时 Pool layout 核验请求。"""

    engine: str
    layout_contract_version: str


class PoolLayoutProbeStatus(StrEnum):
    READY = "READY"
    NOT_CAPABLE = "NOT_CAPABLE"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    INVALID = "INVALID"


@dataclass
class PoolLayoutProbeResult:
    """运行时 Pool layout 核验结果。"""

    status: PoolLayoutProbeStatus
    engine: str
    layout_contract_version: str
    preparation_id: str | None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "engine": self.engine,
            "layout_contract_version": self.layout_contract_version,
            "preparation_id": self.preparation_id,
            "evidence": self.evidence,
        }


@dataclass
class PoolLayoutActivationResult:
    """Pool 数据面切换结果。"""

    committed: bool
    status: "PoolLayoutActivationStatus"
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        return {
            "committed": self.committed,
            "status": self.status.value,
            "evidence": self.evidence,
        }


class PoolLayoutActivationStatus(StrEnum):
    """跨 Service/Plugin/HTTP 边界稳定传输的切换状态。"""

    COMMITTED = "COMMITTED"
    ALREADY_COMMITTED = "ALREADY_COMMITTED"
    ACTIVE_ENTRY_CONFLICT = "ACTIVE_ENTRY_CONFLICT"
    DATA_INCONSISTENT = "DATA_INCONSISTENT"
    INVALID = "INVALID"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    POST_CUTOVER_SYNC_PENDING = "POST_CUTOVER_SYNC_PENDING"
    NOT_ATOMIC = "NOT_ATOMIC"
    UNKNOWN = "UNKNOWN"


@dataclass
class PoolMappingPublishResult:
    """Pool mapping 全量发布结果。"""

    published: bool
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        return {"published": self.published, "evidence": self.evidence}


@dataclass
class PoolMappingVerificationResult:
    """Pool mapping 当前状态验证结果。"""

    valid: bool
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        return {"valid": self.valid, "evidence": self.evidence}


__all__ = [
    "CenterEnsureFailure",
    "CenterEnsureItem",
    "CenterEnsureRequest",
    "CenterEnsureResult",
    "CleanSymlinksRequest",
    "CleanSymlinksResult",
    "PoolLayoutActivateRequest",
    "PoolLayoutRollbackRequest",
    "PoolLayoutActivationResult",
    "PoolLayoutActivationStatus",
    "PoolLayoutProbeRequest",
    "PoolLayoutProbeResult",
    "PoolLayoutProbeStatus",
    "PoolMappingPublishResult",
    "PoolMappingVerificationResult",
    "Skill",
    "SkillConfig",
    "SkillExecutionRequest",
    "SkillExecutionResult",
    "SkillStatus",
    "SkillType",
    "SymlinkItem",
    "SyncBindPathsRequest",
    "SyncSymlinksRequest",
    "SyncSymlinksResult",
]
