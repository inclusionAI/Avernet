"""
SkillsService Protocol — skills management interface.

Each engine implementation under engines/<name>/skills.py provides a class that
structurally satisfies this Protocol. EngineManager exposes the active engine's
skills plugin via `EngineManager.get_instance().skills` (None if the engine
does not declare any skills capabilities).

The Protocol carries two parallel surfaces:

* **per-skill** ops (`list_skills`, `install_skill`, …) — used by engines
  whose runtime tracks one skill at a time (relay-driven aicoding).
* **bulk-symlink** ops (`sync_symlinks`, `sync_bindpaths`, `clean_symlinks`)
  — used by engines that materialise skills as filesystem symlinks and
  reconcile the whole directory in one call (current OpenClaw deployment).

Engines implement only the surface they need; the unsupported methods
should raise :class:`CapabilityNotSupportedError`.

See src/engine/docs/heterogeneous-engine-architecture.md §7.1.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.engine.context import AuthContext
from engine.community.core.skills.models import (
    CenterEnsureRequest,
    CenterEnsureResult,
    CleanSymlinksRequest,
    CleanSymlinksResult,
    PoolLayoutActivateRequest,
    PoolLayoutActivationResult,
    PoolLayoutProbeRequest,
    PoolLayoutProbeResult,
    PoolMappingPublishResult,
    PoolMappingVerificationResult,
    Skill,
    SkillConfig,
    SkillExecutionRequest,
    SkillExecutionResult,
    SyncBindPathsRequest,
    SyncSymlinksRequest,
    SyncSymlinksResult,
    SymlinkItem,
)


@runtime_checkable
class SkillsService(Protocol):
    """Backend talks to skills-capable engines through this Protocol."""

    # ── Per-skill management ──
    async def list_skills(
        self, auth: AuthContext | None = None,
    ) -> list[Skill]:
        """List all skills known to the engine, installed or otherwise."""
        ...

    async def get_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> Skill | None:
        """Look up a single skill by id."""
        ...

    async def install_skill(
        self, config: SkillConfig, auth: AuthContext | None = None,
    ) -> Skill:
        """Install a skill from the given configuration."""
        ...

    async def uninstall_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> bool:
        """Uninstall a skill. Returns True if it was installed and is now removed."""
        ...

    async def update_skill(
        self,
        skill_id: str,
        config: SkillConfig,
        auth: AuthContext | None = None,
    ) -> Skill:
        """Update an installed skill's configuration."""
        ...

    async def enable_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> bool:
        """Enable a previously disabled skill."""
        ...

    async def disable_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> bool:
        """Disable a skill without uninstalling it."""
        ...

    # ── Skill execution ──
    async def execute_skill(
        self,
        request: SkillExecutionRequest,
        auth: AuthContext | None = None,
    ) -> SkillExecutionResult:
        """Invoke a skill action with the given parameters and context."""
        ...

    async def validate_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> list[str]:
        """Validate a skill's configuration. Returns an empty list when valid,
        otherwise a list of human-readable error messages."""
        ...

    # ── Skill discovery ──
    async def discover_skills(
        self, source: str, auth: AuthContext | None = None,
    ) -> list[Skill]:
        """Enumerate skills available to install from the given source."""
        ...

    # ── Bulk symlink reconciliation (OpenClaw-style) ──
    async def sync_symlinks(
        self,
        request: SyncSymlinksRequest,
        auth: AuthContext | None = None,
    ) -> SyncSymlinksResult:
        """Reconcile relative-path symlinks under a single base directory.

        Implementations create / update / remove links to make the
        on-disk state match `request.symlinks` exactly.
        """
        ...

    async def sync_bindpaths(
        self,
        request: SyncBindPathsRequest,
        auth: AuthContext | None = None,
    ) -> SyncSymlinksResult:
        """Reconcile absolute-path symlinks (no shared base dir).

        When `request.clean_target_dir` is true, also strip stale symlinks
        from each unique parent of the desired targets.
        """
        ...

    async def clean_symlinks(
        self,
        request: CleanSymlinksRequest,
        auth: AuthContext | None = None,
    ) -> CleanSymlinksResult:
        """Remove every symlink under each directory in the request."""
        ...

    async def ensure_center_skills(
        self,
        request: CenterEnsureRequest,
        auth: AuthContext | None = None,
    ) -> CenterEnsureResult:
        """Ensure each (skill_uuid, version) is present in the engine's local
        skills-center directory; pull from the bolt_shared NAS source if missing.

        Idempotent: items already present are returned in `ok` without IO.
        Items that fail to materialise (NAS source missing, rsync error) are
        returned in `failed` with a human-readable reason; individual failures
        do not abort the batch.
        """
        ...

    async def activate_pool_layout(
        self,
        request: PoolLayoutActivateRequest,
        auth: AuthContext | None = None,
    ) -> PoolLayoutActivationResult:
        """核对登记事实、同步完整 local 并原子提交 Legacy→Pool bridge。"""
        ...

    async def probe_pool_layout(
        self,
        request: PoolLayoutProbeRequest,
        auth: AuthContext | None = None,
    ) -> PoolLayoutProbeResult:
        """核验当前运行时的 Pool layout 能力与事实。"""
        ...

    async def publish_pool_mappings(
        self,
        mappings: list[SymlinkItem],
        auth: AuthContext | None = None,
    ) -> PoolMappingPublishResult:
        """发布目标 Pool layout 的完整受管 mapping。"""
        ...

    async def verify_pool_mappings(
        self,
        mappings: list[SymlinkItem],
        auth: AuthContext | None = None,
    ) -> PoolMappingVerificationResult:
        """验证当前受管入口精确指向请求中的 Pool source。"""
        ...


__all__ = ["SkillsService"]
