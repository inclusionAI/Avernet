"""Resolve one complete, transport-independent Skill/MCP runtime projection.

The resolver is deliberately a pure domain service.  Its only input is the
already-authorized desired state: active Installation assets, active normal
SkillSet members, System Defaults, and the MCP/CLI facts those inputs select.
It neither reads legacy Default exclusions nor treats a runtime result as
desired state.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.skills_pool.mapping_intent import (
    RuntimeMappingNameConflictError,
    build_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
    RegisteredSkillAsset,
)


class RuntimeNameConflictError(ValueError):
    """Two distinct desired assets resolve to one runtime name."""


@dataclass(frozen=True, slots=True)
class RuntimeDesiredState:
    """The complete, canonical input to one runtime reconciliation."""

    skills: tuple[RegisteredSkillAsset, ...]
    installed_mcp_server_codes: frozenset[str] = frozenset()
    system_default_cli_commands: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeProjection:
    """Engine-neutral full snapshot; adapters must apply it as a whole."""

    skill_mappings: tuple[PoolSkillMapping, ...]
    skill_assets: tuple[RegisteredSkillAsset, ...]
    mcp_server_codes: tuple[str, ...]
    cli_commands: tuple[str, ...]


class RuntimeNamePolicy:
    """The sole policy for logical runtime names.

    ``ac_skill.name`` is the authority.  Locators are content addresses and
    must never influence the visible/runtime entry name.
    """

    @staticmethod
    def name_for(asset: RegisteredSkillAsset) -> str:
        name = asset.name
        if not isinstance(name, str) or not name or name.strip() != name:
            raise ValueError("invalid ac_skill.name for runtime projection")
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("unsafe ac_skill.name for runtime projection")
        return name


class RuntimeProjectionResolver:
    """Build the deduplicated Local/Repo/Center/MCP/CLI snapshot."""

    def resolve(self, state: RuntimeDesiredState) -> RuntimeProjection:
        # Validate all names before mapping so unsupported/ambiguous desired
        # state cannot be partially delivered by an Engine Adapter.
        assets = tuple(state.skills)
        for asset in assets:
            RuntimeNamePolicy.name_for(asset)
            if not asset.git_path.startswith(("local://", "git://", "center://")):
                raise ValueError("unsupported skill source in runtime projection")
        try:
            mappings = build_logical_skill_mappings(list(assets))
        except RuntimeMappingNameConflictError as exc:
            raise RuntimeNameConflictError() from exc
        return RuntimeProjection(
            skill_mappings=tuple(mappings),
            skill_assets=assets,
            mcp_server_codes=tuple(sorted(state.installed_mcp_server_codes)),
            cli_commands=tuple(dict.fromkeys(state.system_default_cli_commands)),
        )


__all__ = [
    "RuntimeDesiredState",
    "RuntimeNameConflictError",
    "RuntimeNamePolicy",
    "RuntimeProjection",
    "RuntimeProjectionResolver",
]
