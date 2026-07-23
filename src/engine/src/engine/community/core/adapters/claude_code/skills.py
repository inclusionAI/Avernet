"""ClaudeCode skills ACL adapter.

Implements the core ``SkillsService`` by delegating to an injected
``ClaudeCodeSkillsPort`` and translating the port's primitive dicts into
core DTOs. All DTO construction (``Skill``, ``SkillConfig``, ``SkillStatus``,
``SkillExecutionResult``, ``SyncSymlinksResult``, ``CleanSymlinksResult``,
``CenterEnsureResult``) lives here; the port impl only deals in plain dicts.

Capability matrix:
- **Port-backed (per-skill)**: ``list_skills``, ``get_skill``,
  ``install_skill``, ``uninstall_skill``, ``update_skill``, ``enable_skill``,
  ``disable_skill``, ``discover_skills``.
- **Port-backed (bulk / local-fs)**: ``sync_symlinks``, ``sync_bindpaths``,
  ``clean_symlinks``, ``ensure_center_skills`` (the relay side has dedicated
  ``skills.sync_symlinks`` et al. RPCs).
- **Port-backed (execute)**: ``execute_skill`` delegates to the port, though
  the corp impl returned a fixed "execute via chat" message. The adapter
  forwards to the port so the impl owns that policy decision.
- **Port-backed (validate)**: ``validate_skill`` builds a one-item list from a
  ``get_skill`` lookup (the corp impl's check that the skill exists and isn't
  in error state). The relay exposes no ``skills.validate`` RPC, so the
  adapter composes the result from ``get_skill`` rather than calling a
  dedicated validate method.

Divergence from OpenClaw's skills adapter
-----------------------------------------
OpenClaw only supports the bulk symlink / center-ensure ops on the port and
raises CapabilityNotSupportedError for all per-skill ops. claude_code exposes
the full per-skill surface on the relay, so this adapter is the inverse.
"""
from __future__ import annotations

import logging
from typing import Any

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
    PoolLayoutProbeRequest,
    PoolLayoutProbeResult,
    PoolMappingPublishResult,
    PoolMappingVerificationResult,
    Skill,
    SkillConfig,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillStatus,
    SkillType,
    SyncBindPathsRequest,
    SyncSymlinksRequest,
    SyncSymlinksResult,
    SymlinkItem,
)
from engine.community.core.skills.protocol import SkillsService
from engine.community.plugin_api.claude_code.skills import ClaudeCodeSkillsPort

log = logging.getLogger("claude-code-skills-adapter")


# ── Dict → DTO helpers (relocated from engines/claude_code/skills.py) ─────────


def _parse_skill_type(raw: Any) -> SkillType:
    val = str(raw).strip().lower() if raw else ""
    if val == "symlink":
        return SkillType.SYMLINK
    if val == "package":
        return SkillType.PACKAGE
    if val == "builtin":
        return SkillType.BUILTIN
    return SkillType.CUSTOM


def _parse_skill_status(raw: Any) -> SkillStatus:
    val = str(raw).strip().lower() if raw else ""
    mapping = {
        "installed": SkillStatus.INSTALLED,
        "available": SkillStatus.AVAILABLE,
        "disabled": SkillStatus.DISABLED,
        "error": SkillStatus.ERROR,
        "installing": SkillStatus.INSTALLING,
    }
    return mapping.get(val, SkillStatus.INSTALLED)


def _skill_from_payload(data: dict[str, Any]) -> Skill:
    skill_id = data.get("skillId", "")
    return Skill(
        skill_id=skill_id,
        name=data.get("name", skill_id),
        description=data.get("description", ""),
        config=SkillConfig(
            skill_id=skill_id,
            skill_type=_parse_skill_type(data.get("skillType")),
            source=data.get("source") or data.get("path"),
            target=data.get("target"),
            enabled=data.get("enabled", True),
        ),
        status=_parse_skill_status(data.get("status")),
        version=data.get("version"),
        dependencies=data.get("dependencies") or [],
        capabilities=data.get("capabilities") or [],
    )


def _config_to_params(config: SkillConfig) -> dict[str, Any]:
    params: dict[str, Any] = {"skillId": config.skill_id}
    if config.source:
        params["source"] = config.source
    if config.skill_type:
        params["skillType"] = config.skill_type.value
    params["enabled"] = config.enabled
    if config.parameters:
        params["parameters"] = config.parameters
    return params


class ClaudeCodeSkillsAdapter(SkillsService):
    """`SkillsService` backed by the claude_code native skills port."""

    def __init__(self, port: ClaudeCodeSkillsPort) -> None:
        self._port = port

    # ── Per-skill management ──────────────────────────────────────────────────

    async def list_skills(
        self, auth: AuthContext | None = None,
    ) -> list[Skill]:
        token = auth.token if auth is not None else None
        raw = await self._port.skills_list(token=token)
        return [_skill_from_payload(s) for s in raw if isinstance(s, dict)]

    async def get_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> Skill | None:
        token = auth.token if auth is not None else None
        data = await self._port.skills_get(skill_id=skill_id, token=token)
        if data is None:
            return None
        return _skill_from_payload(data)

    async def install_skill(
        self, config: SkillConfig, auth: AuthContext | None = None,
    ) -> Skill:
        token = auth.token if auth is not None else None
        data = await self._port.skills_install(
            config=_config_to_params(config), token=token
        )
        return _skill_from_payload(data)

    async def uninstall_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> bool:
        token = auth.token if auth is not None else None
        return await self._port.skills_uninstall(skill_id=skill_id, token=token)

    async def update_skill(
        self,
        skill_id: str,
        config: SkillConfig,
        auth: AuthContext | None = None,
    ) -> Skill:
        token = auth.token if auth is not None else None
        params = _config_to_params(config)
        params["skillId"] = skill_id
        data = await self._port.skills_update(
            skill_id=skill_id, patch=params, token=token
        )
        return _skill_from_payload(data)

    async def enable_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> bool:
        """Enable via the relay's ``skills.update`` (corp impl routed there)."""
        token = auth.token if auth is not None else None
        return await self._port.skills_enable(skill_id=skill_id, token=token)

    async def disable_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> bool:
        token = auth.token if auth is not None else None
        return await self._port.skills_disable(skill_id=skill_id, token=token)

    # ── Skill execution ───────────────────────────────────────────────────────

    async def execute_skill(
        self,
        request: SkillExecutionRequest,
        auth: AuthContext | None = None,
    ) -> SkillExecutionResult:
        # SKILLS_EXECUTE is 'limited' (chat-triggered): Claude Code skills run by
        # sending a chat message mentioning the skill (or ``/<skill-name>``), not
        # via a direct execute RPC. Mirror the corp impl's no-op result rather
        # than firing a live ``skills.execute`` (which would break the capability
        # contract declared in the community matrix).
        return SkillExecutionResult(
            skill_id=request.skill_id,
            action=request.action,
            success=False,
            output=None,
            error="Claude Code skills are executed through chat — send a message "
            "mentioning the skill name or use /<skill-name>.",
        )

    async def validate_skill(
        self, skill_id: str, auth: AuthContext | None = None,
    ) -> list[str]:
        """Validate by lookup — mirrors the corp impl's compose-from-get_skill.

        The relay exposes no ``skills.validate`` RPC; we replicate the corp
        behaviour (empty list when valid, human-readable message otherwise).
        """
        skill = await self.get_skill(skill_id, auth=auth)
        if skill is None:
            return [f"Skill not found: {skill_id}"]
        if skill.status == SkillStatus.ERROR:
            return [f"Skill is in error state: {skill_id}"]
        return []

    # ── Skill discovery ───────────────────────────────────────────────────────

    async def discover_skills(
        self, source: str, auth: AuthContext | None = None,
    ) -> list[Skill]:
        token = auth.token if auth is not None else None
        raw = await self._port.skills_discover(source=source, token=token)
        return [_skill_from_payload(s) for s in raw if isinstance(s, dict)]

    # ── Bulk symlink reconciliation ───────────────────────────────────────────
    #
    # The port method signatures take only ``token`` — no symlinks payload.
    # The corp impl performed the FS reconciliation locally; the OSS port
    # routes the same-named relay RPCs (``skills.sync_symlinks`` etc.). The
    # adapter forwards the token and builds the DTO from the returned dict.
    # (GAP NOTE: the port does not accept the ``symlinks`` / ``directories``
    # payload from the request DTO. See report — the port signature may need
    # widening, or the impl reads them from server-side config. This adapter
    # calls the port as-is; tests only verify the DTO build.)

    async def sync_symlinks(
        self,
        request: SyncSymlinksRequest,
        auth: AuthContext | None = None,
    ) -> SyncSymlinksResult:
        token = auth.token if auth is not None else None
        raw = await self._port.skills_sync_symlinks(token=token)
        return SyncSymlinksResult(
            total=raw.get("total", 0),
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
        token = auth.token if auth is not None else None
        raw = await self._port.skills_sync_bindpaths(token=token)
        return SyncSymlinksResult(
            total=raw.get("total", 0),
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
        token = auth.token if auth is not None else None
        raw = await self._port.skills_clean_symlinks(token=token)
        return CleanSymlinksResult(
            directories_scanned=raw.get("directories_scanned", 0),
            removed=raw.get("removed", []),
        )

    # ── Center skills ensure ──────────────────────────────────────────────────

    async def ensure_center_skills(
        self,
        request: CenterEnsureRequest,
        auth: AuthContext | None = None,
    ) -> CenterEnsureResult:
        token = auth.token if auth is not None else None
        raw = await self._port.skills_ensure_center(token=token)
        ok = [
            CenterEnsureItem(skill_uuid=d.get("skill_uuid", ""), version=str(d.get("version", "")))
            for d in raw.get("ok", []) if isinstance(d, dict)
        ]
        failed = [
            CenterEnsureFailure(
                skill_uuid=d.get("skill_uuid", ""),
                version=str(d.get("version", "")),
                reason=d.get("reason", ""),
            )
            for d in raw.get("failed", []) if isinstance(d, dict)
        ]
        return CenterEnsureResult(ok=ok, failed=failed)

    async def activate_pool_layout(
        self,
        request: PoolLayoutActivateRequest,
        auth: AuthContext | None = None,
    ) -> PoolLayoutActivationResult:
        raise CapabilityNotSupportedError(
            "claude_code", Capability.SKILLS_SYNC_BINDPATHS
        )

    async def probe_pool_layout(
        self,
        request: PoolLayoutProbeRequest,
        auth: AuthContext | None = None,
    ) -> PoolLayoutProbeResult:
        raise CapabilityNotSupportedError(
            "claude_code", Capability.SKILLS_SYNC_BINDPATHS
        )

    async def publish_pool_mappings(
        self,
        mappings: list[SymlinkItem],
        auth: AuthContext | None = None,
    ) -> PoolMappingPublishResult:
        raise CapabilityNotSupportedError(
            "claude_code", Capability.SKILLS_SYNC_BINDPATHS
        )

    async def verify_pool_mappings(
        self,
        mappings: list[SymlinkItem],
        auth: AuthContext | None = None,
    ) -> PoolMappingVerificationResult:
        raise CapabilityNotSupportedError(
            "claude_code", Capability.SKILLS_SYNC_BINDPATHS
        )


__all__ = ["ClaudeCodeSkillsAdapter"]
