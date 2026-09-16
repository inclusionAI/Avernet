"""Read-only SkillSet resource projection helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    LegacySkillSetCompatibilityFactoryProtocol,
    list_skill_set_mcp_projection,
)
from agentclaw.community.plugin_api.passport import PassportPlugin, extract_cli_items


def list_skill_set_resources(
    *,
    repository: CapabilityDesiredStateRepositoryProtocol,
    legacy_factory: LegacySkillSetCompatibilityFactoryProtocol,
    passport: PassportPlugin,
    bot: Mapping[str, Any],
    bot_id: str,
    owner_id: str,
    engine_type: str,
    default_engine_types: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Project existing SkillSet resources with current Passport display metadata."""
    try:
        snapshot = passport.query_agent_passport(
            bot_id, str(bot.get("entity_id") or owner_id)
        )
        default_clis = extract_cli_items(snapshot)
        default_mcp_metadata = _passport_mcp_display_metadata(snapshot)
    except Exception:
        default_clis = []
        default_mcp_metadata = {}
    items = repository.list_sets(
        bot_id=bot_id,
        owner_id=owner_id,
        engine_type=engine_type,
        default_engine_types=default_engine_types,
    )
    resources: list[dict[str, Any]] = []
    for item in items:
        mcps = list_skill_set_mcp_projection(
            repository=repository,
            legacy_factory=legacy_factory,
            bot=bot,
            bot_id=bot_id,
            owner_id=owner_id,
            target=item,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
        )
        if item["is_default"]:
            mcps = _enrich_default_mcp_display(mcps, default_mcp_metadata)
        resources.append(
            {
                **item,
                "mcps": mcps,
                "clis": default_clis if item["is_default"] else [],
            }
        )
    return resources


def _passport_mcp_display_metadata(
    passport: Mapping[str, Any] | None,
) -> dict[str, dict[str, str]]:
    """Index the usable display metadata in one Passport snapshot."""
    if not isinstance(passport, Mapping):
        return {}

    metadata: dict[str, dict[str, str]] = {}
    for item in passport.get("mcps") or []:
        if not isinstance(item, Mapping):
            continue
        server_code = item.get("mcp_code")
        if not isinstance(server_code, str) or not server_code:
            continue
        display = {
            key: value
            for key, value in (
                ("name", item.get("mcp_name")),
                ("description", item.get("mcp_desc")),
            )
            if isinstance(value, str) and value
        }
        if display:
            metadata[server_code] = display
    return metadata


def _enrich_default_mcp_display(
    mcps: Sequence[Mapping[str, Any]],
    metadata_by_code: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    """Overlay Passport labels without changing the existing MCP projection."""
    enriched: list[dict[str, Any]] = []
    for mcp in mcps:
        item = dict(mcp)
        server_code = item.get("server_code")
        metadata = (
            metadata_by_code.get(server_code) if isinstance(server_code, str) else None
        )
        if metadata:
            item.update(metadata)
        enriched.append(item)
    return enriched
