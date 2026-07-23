"""OpenClaw skills ACL adapter.

Implements the core ``SkillsService`` by delegating to an injected
``OpenClawSkillsPort`` and translating the port's primitive dicts into core
DTOs.  All DTO construction (``CenterEnsureResult``, ``SyncSymlinksResult``,
``CleanSymlinksResult``) lives here; the port impl only deals in plain dicts.

``SkillsConflictError`` and ``SkillsValidationError`` are re-exported from
the legacy engine module — routers map them to HTTP 409 / 400 as before.
In this layer they propagate unchanged from the port (which raises plain
``RuntimeError`` / ``ValueError`` subclasses); the adapter does not wrap or
catch them.

Capability matrix:
- **Port-backed**: ``ensure_center_skills``, ``sync_symlinks``,
  ``sync_bindpaths``, ``clean_symlinks`` (4 ops).
- **Raises CapabilityNotSupportedError** (not on the port — decision 5):
  ``list_skills``, ``get_skill``, ``install_skill``, ``uninstall_skill``,
  ``update_skill``, ``enable_skill``, ``disable_skill``, ``execute_skill``,
  ``validate_skill``, ``discover_skills`` (10 ops).
"""
from __future__ import annotations

from engine.community.core.engine.capability import Capability
from engine.community.core.engine.context import AuthContext
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.skills.models import (
    CenterEnsureFailure,
    CenterEnsureItem,
    CenterEnsureRequest,
    CenterEnsureResult,
    CleanSymlinksRequest,
    CleanSymlinksResult,
    PoolLayoutActivateRequest,
    PoolLayoutActivationResult,
    PoolLayoutActivationStatus,
    PoolLayoutProbeRequest,
    PoolLayoutProbeResult,
    PoolLayoutProbeStatus,
    PoolLayoutRollbackRequest,
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
from engine.community.core.skills.protocol import SkillsService
from engine.community.plugin_api.openclaw.skills import OpenClawSkillsPort


class OpenClawSkillsAdapter(SkillsService):
    """`SkillsService` backed by the OpenClaw native skills port."""

    def __init__(self, port: OpenClawSkillsPort) -> None:
        self._port = port

    # ── Bulk symlink / center-ensure (port-backed) ────────────────────────────

    async def ensure_center_skills(
        self,
        request: CenterEnsureRequest,
        auth: AuthContext | None = None,
    ) -> CenterEnsureResult:
        params = {
            "items": [
                {"skill_uuid": item.skill_uuid, "version": item.version}
                for item in request.items
            ]
        }
        raw = await self._port.ensure_center_skills(params)
        ok = [
            CenterEnsureItem(skill_uuid=d["skill_uuid"], version=d["version"])
            for d in raw.get("ok", [])
        ]
        failed = [
            CenterEnsureFailure(
                skill_uuid=d["skill_uuid"],
                version=d["version"],
                reason=d["reason"],
            )
            for d in raw.get("failed", [])
        ]
        return CenterEnsureResult(ok=ok, failed=failed)

    async def sync_symlinks(
        self,
        request: SyncSymlinksRequest,
        auth: AuthContext | None = None,
    ) -> SyncSymlinksResult:
        params = {
            "symlinks": [
                {"source": item.source, "target": item.target}
                for item in (request.symlinks or [])
            ]
        }
        raw = await self._port.sync_symlinks(params)
        return SyncSymlinksResult(
            total=raw["total"],
            created=raw.get("created", []),
            updated=raw.get("updated", []),
            kept=raw.get("kept", []),
            removed=raw.get("removed", []),
            base_dir=raw.get("base_dir"),
        )

    async def sync_bindpaths(
        self,
        request: SyncBindPathsRequest,
        auth: AuthContext | None = None,
    ) -> SyncSymlinksResult:
        params = {
            "symlinks": [
                {"source": item.source, "target": item.target}
                for item in (request.symlinks or [])
            ],
            "clean_target_dir": request.clean_target_dir,
        }
        raw = await self._port.sync_bindpaths(params)
        return SyncSymlinksResult(
            total=raw["total"],
            created=raw.get("created", []),
            updated=raw.get("updated", []),
            kept=raw.get("kept", []),
            removed=raw.get("removed", []),
            base_dir=raw.get("base_dir"),
        )

    async def clean_symlinks(
        self,
        request: CleanSymlinksRequest,
        auth: AuthContext | None = None,
    ) -> CleanSymlinksResult:
        params = {"directories": list(request.directories or [])}
        raw = await self._port.clean_symlinks(params)
        return CleanSymlinksResult(
            directories_scanned=raw["directories_scanned"],
            removed=raw.get("removed", []),
        )

    async def activate_pool_layout(
        self,
        request: PoolLayoutActivateRequest,
        auth: AuthContext | None = None,
    ) -> PoolLayoutActivationResult:
        raw = await self._port.activate_pool_layout(
            {
                "migration_generation": request.migration_generation,
                "preparation_id": request.preparation_id,
                "registered_local_names": request.registered_local_names,
                "mappings": [
                    {"source": item.source, "target": item.target}
                    for item in request.mappings
                ],
            }
        )
        raw_status = str(raw.get("status", ""))
        try:
            status = PoolLayoutActivationStatus(raw_status)
        except ValueError:
            status = PoolLayoutActivationStatus.UNKNOWN
        evidence = dict(raw.get("evidence") or {})
        if status is PoolLayoutActivationStatus.UNKNOWN:
            evidence["raw_status"] = raw_status
        committed = (
            raw.get("committed") is True
            and status
            in {
                PoolLayoutActivationStatus.COMMITTED,
                PoolLayoutActivationStatus.ALREADY_COMMITTED,
            }
        )
        return PoolLayoutActivationResult(
            committed=committed,
            status=status,
            evidence=evidence,
        )

    async def probe_pool_layout(
        self,
        request: PoolLayoutProbeRequest,
        auth: AuthContext | None = None,
    ) -> PoolLayoutProbeResult:
        raw = await self._port.probe_pool_layout(
            {
                "engine": request.engine,
                "layout_contract_version": request.layout_contract_version,
            }
        )
        return PoolLayoutProbeResult(
            status=PoolLayoutProbeStatus(str(raw["status"])),
            engine=str(raw["engine"]),
            layout_contract_version=str(raw["layout_contract_version"]),
            preparation_id=(
                str(raw["preparation_id"])
                if raw.get("preparation_id") is not None
                else None
            ),
            evidence=dict(raw.get("evidence") or {}),
        )

    async def rollback_pool_layout(
        self,
        request: PoolLayoutRollbackRequest,
        auth: AuthContext | None = None,
    ) -> PoolLayoutActivationResult:
        raw = await self._port.rollback_pool_layout(
            {
                "rollback_generation": request.rollback_generation,
                "registered_local_names": request.registered_local_names,
            }
        )
        raw_status = str(raw.get("status", ""))
        try:
            status = PoolLayoutActivationStatus(raw_status)
        except ValueError:
            status = PoolLayoutActivationStatus.UNKNOWN
        evidence = dict(raw.get("evidence") or {})
        if status is PoolLayoutActivationStatus.UNKNOWN:
            evidence["raw_status"] = raw_status
        return PoolLayoutActivationResult(
            committed=(
                raw.get("committed") is True
                and status
                in {
                    PoolLayoutActivationStatus.COMMITTED,
                    PoolLayoutActivationStatus.ALREADY_COMMITTED,
                }
            ),
            status=status,
            evidence=evidence,
        )

    async def publish_pool_mappings(
        self,
        mappings: list[SymlinkItem],
        auth: AuthContext | None = None,
    ) -> PoolMappingPublishResult:
        raw = await self._port.publish_pool_mappings(
            {
                "mappings": [
                    {"source": item.source, "target": item.target}
                    for item in mappings
                ]
            }
        )
        return PoolMappingPublishResult(
            published=raw.get("published") is True,
            evidence=dict(raw.get("evidence") or {}),
        )

    async def verify_pool_mappings(
        self,
        mappings: list[SymlinkItem],
        auth: AuthContext | None = None,
    ) -> PoolMappingVerificationResult:
        raw = await self._port.verify_pool_mappings(
            {
                "mappings": [
                    {"source": item.source, "target": item.target}
                    for item in mappings
                ]
            }
        )
        return PoolMappingVerificationResult(
            valid=raw.get("valid") is True,
            evidence=dict(raw.get("evidence") or {}),
        )

    # ── Per-skill ops (not exposed by OpenClaw) ───────────────────────────────

    async def list_skills(
        self, auth: AuthContext | None = None,
    ) -> list[Skill]:
        raise CapabilityNotSupportedError("openclaw", Capability.SKILLS_LIST)

    async def get_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> Skill | None:
        raise CapabilityNotSupportedError("openclaw", Capability.SKILLS_LIST)

    async def install_skill(
        self, config: SkillConfig, auth: AuthContext | None = None,
    ) -> Skill:
        raise CapabilityNotSupportedError("openclaw", Capability.SKILLS_INSTALL)

    async def uninstall_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> bool:
        raise CapabilityNotSupportedError("openclaw", Capability.SKILLS_UNINSTALL)

    async def update_skill(
        self,
        skill_id: str,
        config: SkillConfig,
        auth: AuthContext | None = None,
    ) -> Skill:
        raise CapabilityNotSupportedError("openclaw", Capability.SKILLS_UPDATE)

    async def enable_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> bool:
        raise CapabilityNotSupportedError("openclaw", Capability.SKILLS_INSTALL)

    async def disable_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> bool:
        raise CapabilityNotSupportedError("openclaw", Capability.SKILLS_UNINSTALL)

    async def execute_skill(
        self,
        request: SkillExecutionRequest,
        auth: AuthContext | None = None,
    ) -> SkillExecutionResult:
        raise CapabilityNotSupportedError("openclaw", Capability.SKILLS_EXECUTE)

    async def validate_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> list[str]:
        raise CapabilityNotSupportedError("openclaw", Capability.SKILLS_LIST)

    async def discover_skills(
        self, source: str, auth: AuthContext | None = None,
    ) -> list[Skill]:
        raise CapabilityNotSupportedError("openclaw", Capability.SKILLS_DISCOVER)


__all__ = ["OpenClawSkillsAdapter"]
