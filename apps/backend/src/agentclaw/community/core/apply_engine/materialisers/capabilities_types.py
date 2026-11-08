"""Private values passed only between the capability materialiser's three stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from agentclaw.community.core.apply_engine.desired_state import DesiredCapabilities
from agentclaw.community.core.apply_engine.registry import CategoryPlan
from agentclaw.community.core.skill_center.skill_reference_contract import (
    PublicSkillPreview,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    SkillSetApplySnapshot,
    SkillSetSnapshot,
)


@dataclass(frozen=True)
class Skill:
    name: str
    key: str
    skill_id: str | None
    dependencies: frozenset[str]
    package: bytes | None = None
    preview: PublicSkillPreview | None = None
    note: str | None = None


@dataclass(frozen=True)
class SkillSet:
    name: str
    description: str | None
    is_active: bool
    skills: tuple[Skill, ...]
    mcp: tuple[str, ...]


@dataclass(frozen=True)
class Resolved:
    target: DesiredCapabilities
    snapshot: SkillSetApplySnapshot
    direct: tuple[Skill, ...] = ()
    sets: tuple[SkillSet, ...] = ()
    default_change: DefaultExclusionChange = DefaultExclusionChange()
    default_enabled: bool = False
    final_skills: tuple[Skill, ...] = ()
    set_codes: frozenset[str] = frozenset()
    mcp: tuple[tuple[str, dict | None], ...] = ()


@dataclass(frozen=True, kw_only=True)
class CapabilityPlan(CategoryPlan):
    # Required only on plans constructed by this module; never generic currency.
    resolved: Resolved
    remove_sets: tuple[str, ...] = ()
    remove_skills: tuple[str, ...] = ()
    cleanup_ids: frozenset[int] = frozenset()
    remove_mcps: tuple[str, ...] = ()
    set_write: bool = False


def assets(snapshot: SkillSetApplySnapshot) -> dict[int, dict[str, Any]]:
    return {int(item["id"]): item for item in snapshot.skills}


def membership_assets(
    snapshot: SkillSetApplySnapshot, group: SkillSetSnapshot
) -> dict[int, dict[str, Any]]:
    """Map this Set's raw member keys without shadowing real asset IDs."""
    catalog = assets(snapshot)
    by_uuid = {
        item["skill_uuid"]: item for item in snapshot.skills if item.get("skill_uuid")
    }
    references = dict(group.skill_references)
    result = {}
    for raw_id in group.skill_ids:
        uuid = references.get(raw_id)
        asset = by_uuid.get(uuid) if uuid else catalog.get(raw_id)
        if asset is not None:
            result[raw_id] = asset
    return result


def membership_key(snapshot: SkillSetApplySnapshot, group: SkillSetSnapshot, raw_id: int) -> str:
    uuid = dict(group.skill_references).get(raw_id)
    if uuid:
        return center_key(uuid)
    asset = assets(snapshot).get(raw_id)
    return asset_key(asset) if asset is not None else "id:" + str(raw_id)


def existing_member(snapshot: SkillSetApplySnapshot, group: SkillSetSnapshot, raw_id: int) -> Skill:
    asset = membership_assets(snapshot, group).get(raw_id)
    if asset is None:
        raise ValueError("Skill asset is unavailable")
    return existing_skill(snapshot, int(asset["id"]))


def asset_key(asset: dict[str, Any]) -> str:
    if str(asset.get("git_path", "")).startswith("center://"):
        uuid = asset.get("skill_uuid")
        if not uuid:
            raise ValueError("Center Skill has no stable UUID")
        return center_key(uuid)
    return "id:" + str(asset["id"])


def center_key(value: str) -> str:
    return "uuid:" + str(UUID(value))


def local_owned(asset: dict[str, Any], snapshot: SkillSetApplySnapshot) -> bool:
    return (
        str(asset.get("git_path", "")).startswith("local://")
        and asset.get("user_id") == snapshot.scope.owner_id
        and asset.get("bolt_id") == snapshot.scope.bot_id
    )


def existing_skill(snapshot: SkillSetApplySnapshot, skill_id: int) -> Skill:
    asset = assets(snapshot).get(skill_id)
    if asset is None or asset.get("offline_at") is not None:
        raise ValueError("Skill asset is unavailable")
    if asset.get("resolution_error"):
        raise ValueError(str(asset["resolution_error"]))
    return Skill(
        asset["name"], asset_key(asset), str(asset["id"]), frozenset(asset["mcp_codes"])
    )


def effective_sets(snapshot: SkillSetApplySnapshot) -> tuple[set[int], set[str]]:
    skill_ids, codes = set(), set()
    defaults = [item for item in snapshot.sets if item.is_default]
    for item in snapshot.sets:
        if not item.is_active and not item.is_default:
            continue
        skill_ids.update(
            int(asset["id"])
            for raw_id, asset in membership_assets(snapshot, item).items()
            if not item.is_default or (item.id, raw_id) not in snapshot.skill_exclusions
        )
        codes.update(
            code
            for code in item.mcp_codes
            if not item.is_default or (item.id, code) not in snapshot.mcp_exclusions
        )
    for code in snapshot.platform_default_codes:
        if not defaults or any(
            (item.id, code) not in snapshot.mcp_exclusions for item in defaults
        ):
            codes.add(code)
    return skill_ids, codes


def direct_ids(snapshot: SkillSetApplySnapshot) -> set[int]:
    supplied, _ = effective_sets(snapshot)
    catalog = assets(snapshot)
    supplied_keys = {asset_key(a) for i, a in catalog.items() if i in supplied}
    return {
        i
        for i in snapshot.installed_skill_ids
        if i not in supplied
        and (i not in catalog or asset_key(catalog[i]) not in supplied_keys)
    }


def default_delta(
    snapshot: SkillSetApplySnapshot, enabled: bool
) -> DefaultExclusionChange:
    defaults = [item for item in snapshot.sets if item.is_default]
    if not defaults and snapshot.platform_default_codes and not enabled:
        raise ValueError(
            "Default scope is missing; platform policy MCP cannot be excluded"
        )
    skills = frozenset((item.id, sid) for item in defaults for sid in item.skill_ids)
    mcps = frozenset(
        (item.id, code)
        for item in defaults
        for code in set(item.mcp_codes) | snapshot.platform_default_codes
    )
    if enabled:
        # All applicable stale exclusions are reset, but only actual members restore supply.
        return DefaultExclusionChange(
            remove_skills=snapshot.skill_exclusions, remove_mcps=snapshot.mcp_exclusions
        )
    return DefaultExclusionChange(
        add_skills=skills - snapshot.skill_exclusions,
        add_mcps=mcps - snapshot.mcp_exclusions,
    )
