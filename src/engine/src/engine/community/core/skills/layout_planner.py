"""Engine-owned Skills layout descriptors and resolver.

This module is intentionally pure and stdlib-only. It resolves an Engine's
logical layout identity into both Legacy and Pool topology. Callers choose the
authoritative topology for their operation; the resolver does not inspect
runtime state, rollout configuration, markers, or delivery mechanisms.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

LAYOUT_CONTRACT_VERSION = "skills-pool-p3-v1"


class SkillLayoutCapability(StrEnum):
    FILESYSTEM = "filesystem"
    ARTIFACT = "artifact"


class SkillLayoutResolutionError(ValueError):
    """Logical layout identity cannot be resolved safely."""


class UnsupportedLayoutContractError(SkillLayoutResolutionError):
    pass


class UnsupportedRuntimeLayoutError(SkillLayoutResolutionError):
    pass


@dataclass(frozen=True, slots=True)
class LayoutIdentity:
    engine_type: str
    layout_contract_version: str


@dataclass(frozen=True, slots=True)
class RuntimeLayoutContext:
    home: Path = Path("/home/admin")


@dataclass(frozen=True, slots=True)
class _FilesystemTemplate:
    active_root: str
    legacy_local: str
    legacy_repo: str
    pool_root: str
    local_bridge: str
    repo_bridge: str

    def resolve(
        self,
        *,
        identity: LayoutIdentity,
        context: RuntimeLayoutContext,
    ) -> ResolvedFilesystemLayoutPlan:
        home = context.home

        def path(relative: str) -> Path:
            return home / relative

        pool_root = path(self.pool_root)
        return ResolvedFilesystemLayoutPlan(
            engine_type=identity.engine_type,
            layout_contract_version=identity.layout_contract_version,
            active_root=path(self.active_root),
            legacy_local=path(self.legacy_local),
            legacy_repo=path(self.legacy_repo),
            pool_root=pool_root,
            pool_local=pool_root / "skills-local",
            pool_repo=pool_root / "skills-repo",
            ready_marker=pool_root / ".pool-ready",
            active_marker=pool_root / ".pool-active",
            local_bridge=path(self.local_bridge),
            repo_bridge=path(self.repo_bridge),
        )


@dataclass(frozen=True, slots=True)
class EngineSkillLayoutDescriptor:
    engine_type: str
    capability: SkillLayoutCapability
    filesystem: _FilesystemTemplate | None = None

    def resolve(
        self,
        *,
        identity: LayoutIdentity,
        context: RuntimeLayoutContext,
    ) -> ResolvedSkillLayoutPlan:
        if identity.engine_type != self.engine_type:
            raise SkillLayoutResolutionError(
                "descriptor engine does not match layout identity"
            )
        if identity.layout_contract_version != LAYOUT_CONTRACT_VERSION:
            raise UnsupportedLayoutContractError(
                "unsupported Skills layout contract: "
                f"{identity.layout_contract_version}"
            )
        if self.capability is SkillLayoutCapability.ARTIFACT:
            return ResolvedArtifactLayoutPlan(
                engine_type=self.engine_type,
                layout_contract_version=identity.layout_contract_version,
            )
        template = self.filesystem
        if template is None:
            raise UnsupportedRuntimeLayoutError(
                f"{self.engine_type} has no filesystem Skills layout"
            )
        return template.resolve(identity=identity, context=context)


@dataclass(frozen=True, slots=True)
class ResolvedArtifactLayoutPlan:
    engine_type: str
    layout_contract_version: str
    capability: SkillLayoutCapability = SkillLayoutCapability.ARTIFACT


@dataclass(frozen=True, slots=True)
class ResolvedFilesystemLayoutPlan:
    engine_type: str
    layout_contract_version: str
    active_root: Path
    legacy_local: Path
    legacy_repo: Path
    pool_root: Path
    pool_local: Path
    pool_repo: Path
    ready_marker: Path
    active_marker: Path
    local_bridge: Path
    repo_bridge: Path
    capability: SkillLayoutCapability = SkillLayoutCapability.FILESYSTEM

    @property
    def marker(self) -> Path:
        """Compatibility name used by the existing runtime probe."""

        return self.ready_marker

    @classmethod
    def for_engine(
        cls,
        engine: str,
        home: Path,
    ) -> ResolvedFilesystemLayoutPlan:
        """Compatibility constructor for existing Engine Pool consumers."""

        return resolve_filesystem_skill_layout(
            LayoutIdentity(
                engine_type=engine,
                layout_contract_version=LAYOUT_CONTRACT_VERSION,
            ),
            RuntimeLayoutContext(home=home),
        )

    @classmethod
    def for_home(cls, home: Path) -> ResolvedFilesystemLayoutPlan:
        return cls.for_engine("openclaw", home)


ResolvedSkillLayoutPlan = ResolvedFilesystemLayoutPlan | ResolvedArtifactLayoutPlan


def _filesystem_template(
    *,
    engine_home: str,
    active_root: str,
    legacy_local: str,
    legacy_repo: str,
    local_bridge: str,
    repo_bridge: str,
) -> _FilesystemTemplate:
    return _FilesystemTemplate(
        active_root=active_root,
        legacy_local=legacy_local,
        legacy_repo=legacy_repo,
        pool_root=f"{engine_home}/workspace/skills-pool",
        local_bridge=local_bridge,
        repo_bridge=repo_bridge,
    )


_OPENCLAW_FILESYSTEM = _filesystem_template(
    engine_home=".openclaw",
    active_root=".openclaw/workspace/skills",
    legacy_local=".openclaw/workspace/skills/skills-local",
    legacy_repo=".openclaw/workspace/skills/skills-repo",
    local_bridge=".openclaw/workspace/skills/skills-local",
    repo_bridge=".openclaw/workspace/skills/skills-repo",
)
_CLAUDE_FILESYSTEM = _filesystem_template(
    engine_home=".claude_code",
    active_root=".claude/skills",
    legacy_local=".claude_code/workspace/skills/skills-local",
    legacy_repo=".claude_code/skills-repo",
    local_bridge=".claude/skills/skills-local",
    repo_bridge=".claude/skills/skills-repo",
)
_AICODING_FILESYSTEM = _filesystem_template(
    engine_home=".aicoding",
    active_root=".claude/skills",
    legacy_local=".aicoding/workspace/skills/skills-local",
    legacy_repo=".aicoding/skills-repo",
    local_bridge=".claude/skills/skills-local",
    repo_bridge=".aicoding/skills-repo",
)
_HERMES_FILESYSTEM = _filesystem_template(
    engine_home=".hermes",
    active_root=".hermes/skills",
    legacy_local=".hermes/workspace/skills/skills-local",
    legacy_repo=".hermes/skills-repo",
    local_bridge=".hermes/skills/skills-local",
    repo_bridge=".hermes/skills-repo",
)

DESCRIPTORS: dict[str, EngineSkillLayoutDescriptor] = {
    "openclaw": EngineSkillLayoutDescriptor(
        engine_type="openclaw",
        capability=SkillLayoutCapability.FILESYSTEM,
        filesystem=_OPENCLAW_FILESYSTEM,
    ),
    "claude_code": EngineSkillLayoutDescriptor(
        engine_type="claude_code",
        capability=SkillLayoutCapability.FILESYSTEM,
        filesystem=_CLAUDE_FILESYSTEM,
    ),
    "aicoding": EngineSkillLayoutDescriptor(
        engine_type="aicoding",
        capability=SkillLayoutCapability.FILESYSTEM,
        filesystem=_AICODING_FILESYSTEM,
    ),
    "hermes": EngineSkillLayoutDescriptor(
        engine_type="hermes",
        capability=SkillLayoutCapability.FILESYSTEM,
        filesystem=_HERMES_FILESYSTEM,
    ),
    "teclaw": EngineSkillLayoutDescriptor(
        engine_type="teclaw",
        capability=SkillLayoutCapability.ARTIFACT,
    ),
}


def resolve_skill_layout(
    identity: LayoutIdentity,
    context: RuntimeLayoutContext,
) -> ResolvedSkillLayoutPlan:
    descriptor = DESCRIPTORS.get(identity.engine_type)
    if descriptor is None:
        raise SkillLayoutResolutionError(
            f"unknown engine Skills layout: {identity.engine_type}"
        )
    return descriptor.resolve(identity=identity, context=context)


def resolve_filesystem_skill_layout(
    identity: LayoutIdentity,
    context: RuntimeLayoutContext,
) -> ResolvedFilesystemLayoutPlan:
    plan = resolve_skill_layout(identity, context)
    if not isinstance(plan, ResolvedFilesystemLayoutPlan):
        raise UnsupportedRuntimeLayoutError(
            f"{identity.engine_type} has no filesystem Skills layout"
        )
    return plan


__all__ = [
    "DESCRIPTORS",
    "LAYOUT_CONTRACT_VERSION",
    "EngineSkillLayoutDescriptor",
    "LayoutIdentity",
    "ResolvedArtifactLayoutPlan",
    "ResolvedFilesystemLayoutPlan",
    "ResolvedSkillLayoutPlan",
    "RuntimeLayoutContext",
    "SkillLayoutCapability",
    "SkillLayoutResolutionError",
    "UnsupportedLayoutContractError",
    "UnsupportedRuntimeLayoutError",
    "resolve_filesystem_skill_layout",
    "resolve_skill_layout",
]
