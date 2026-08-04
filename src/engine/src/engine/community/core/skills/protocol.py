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
* **Pool layout** ops (`probe_pool_layout`, `activate_pool_layout`,
  `publish_pool_mappings`, `verify_pool_mappings`) — implemented by the
  OpenClaw and Claude Code adapters/ports in this repository. Corp AICoding
  and Hermes composition roots consume the same Protocol, mapping contract,
  and shared Engine-owned layout planner.

Engines implement only the surface they need; the unsupported methods
should raise :class:`CapabilityNotSupportedError`.

The versioned Pool mapping wire contract and compatibility rules are recorded
in ``src/engine/docs/heterogeneous-engine-architecture.md`` §7.3.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from engine.community.core.engine.context import AuthContext
from engine.community.core.skills.exceptions import (
    InvalidPoolMappingRequestError,
)
from engine.community.core.skills.models import (
    CenterEnsureRequest,
    CenterEnsureResult,
    CleanSymlinksRequest,
    CleanSymlinksResult,
    PoolLayoutActivateRequest,
    PoolLayoutActivationResult,
    PoolLayoutProbeRequest,
    PoolLayoutProbeResult,
    PoolLayoutRollbackRequest,
    PoolMappingPublishResult,
    PoolMappingSourceLayout,
    PoolMappingVerificationResult,
    PoolQuarantineCleanupRequest,
    PoolQuarantineCleanupResult,
    PoolSkillMappingIntent,
    Skill,
    SkillConfig,
    SkillExecutionRequest,
    SkillExecutionResult,
    SymlinkItem,
    SyncBindPathsRequest,
    SyncSymlinksRequest,
    SyncSymlinksResult,
)


@runtime_checkable
class SkillsService(Protocol):
    """Backend talks to skills-capable engines through this Protocol."""

    # ── Per-skill management ──
    async def list_skills(
        self,
        auth: AuthContext | None = None,
    ) -> list[Skill]:
        """List all skills known to the engine, installed or otherwise."""
        ...

    async def get_skill(
        self,
        skill_id: str,
        auth: AuthContext | None = None,
    ) -> Skill | None:
        """Look up a single skill by id."""
        ...

    async def install_skill(
        self,
        config: SkillConfig,
        auth: AuthContext | None = None,
    ) -> Skill:
        """Install a skill from the given configuration."""
        ...

    async def uninstall_skill(
        self,
        skill_id: str,
        auth: AuthContext | None = None,
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
        self,
        skill_id: str,
        auth: AuthContext | None = None,
    ) -> bool:
        """Enable a previously disabled skill."""
        ...

    async def disable_skill(
        self,
        skill_id: str,
        auth: AuthContext | None = None,
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
        self,
        skill_id: str,
        auth: AuthContext | None = None,
    ) -> list[str]:
        """Validate a skill's configuration. Returns an empty list when valid,
        otherwise a list of human-readable error messages."""
        ...

    # ── Skill discovery ──
    async def discover_skills(
        self,
        source: str,
        auth: AuthContext | None = None,
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
        """核对登记事实、同步完整 local 并原子提交 Legacy→Pool bridge。

        Raises:
            InvalidPoolMappingRequestError: The versioned mapping request is
                invalid and no filesystem mutation has started.
        """
        ...

    async def rollback_pool_layout(
        self,
        request: PoolLayoutRollbackRequest,
        auth: AuthContext | None = None,
    ) -> PoolLayoutActivationResult:
        """从当前 Pool 重建 Legacy local 并原子切回。"""
        ...

    async def cleanup_pool_quarantine(
        self,
        request: PoolQuarantineCleanupRequest,
        auth: AuthContext | None = None,
    ) -> PoolQuarantineCleanupResult:
        """Delete one exact retained migration generation idempotently."""
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
        mappings: list[PoolSkillMappingIntent | SymlinkItem],
        *,
        retired_mappings: Sequence[PoolSkillMappingIntent | SymlinkItem] = (),
        source_layout: PoolMappingSourceLayout = PoolMappingSourceLayout.POOL,
        mapping_contract_version: str | None = None,
        auth: AuthContext | None = None,
    ) -> PoolMappingPublishResult:
        """Publish a complete mapping set.

        ``skills-pool-mapping-v2`` carries logical intents. An omitted version
        is the compatibility form for legacy physical ``SymlinkItem`` values.

        Raises:
            InvalidPoolMappingRequestError: The version, wire shape, or
                logical mapping is invalid before filesystem publication.
        """
        ...

    async def verify_pool_mappings(
        self,
        mappings: list[PoolSkillMappingIntent | SymlinkItem],
        *,
        retired_mappings: Sequence[PoolSkillMappingIntent | SymlinkItem] = (),
        source_layout: PoolMappingSourceLayout = PoolMappingSourceLayout.POOL,
        mapping_contract_version: str | None = None,
        auth: AuthContext | None = None,
    ) -> PoolMappingVerificationResult:
        """Verify mappings using the same versioned contract as publication.

        Raises:
            InvalidPoolMappingRequestError: The version, wire shape, or
                logical mapping is invalid before filesystem inspection.
        """
        ...


__all__ = ["InvalidPoolMappingRequestError", "SkillsService"]
