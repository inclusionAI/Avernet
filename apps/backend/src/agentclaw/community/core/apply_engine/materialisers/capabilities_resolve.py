"""Resolve capability supply and ownership without changing persistent state."""

from __future__ import annotations


from agentclaw.community.core.apply_engine.desired_state import DefaultSkillSetRef
from agentclaw.community.core.skill_center.skill_set_apply import DefaultExclusionChange
from agentclaw.community.core.apply_engine.registry import ResolveFailure
from .capabilities_types import (
    Resolved,
    SkillSet,
    asset_key,
    assets,
    default_delta,
    direct_ids,
    effective_sets,
    existing_skill,
    existing_member,
    membership_assets,
    membership_key,
)


def resolve_supply(target, snapshot, direct, groups, mcp):
    """Compute the target closure and compare both current and target ownership."""
    failures = []
    enabled = target.skill_sets is None or any(
        isinstance(s, DefaultSkillSetRef) for s in target.skill_sets.entries
    )
    try:
        change = (
            default_delta(snapshot, enabled)
            if target.skill_sets is not None
            else DefaultExclusionChange()
        )
    except ValueError as exc:
        return None, (ResolveFailure("skill_sets/default", str(exc)),)
    catalog = assets(snapshot)
    current_direct = direct_ids(snapshot)
    current_set_ids, current_set_codes = effective_sets(snapshot)
    direct_keys = {asset_key(catalog[i]) for i in current_direct if i in catalog}
    direct_names = {
        catalog[i]["name"]: asset_key(catalog[i])
        for i in current_direct
        if i in catalog
    }
    direct_codes = snapshot.installed_mcp_codes - current_set_codes
    ordinary = [s for s in snapshot.sets if not s.is_default]
    defaults = [s for s in snapshot.sets if s.is_default]
    owners = {}
    code_owners = {}
    for group in ordinary:
        for sid in group.skill_ids:
            key = membership_key(snapshot, group, sid)
            owners.setdefault(key, set()).add(group.name)
        for code in group.mcp_codes:
            code_owners.setdefault(code, set()).add(group.name)
    # The default subtarget None preserves exclusions, empty excludes all snapshot members.
    default_members, default_codes = [], set()
    for group in defaults:
        for sid in group.skill_ids:
            excluded = (group.id, sid) in snapshot.skill_exclusions
            if enabled and (target.skill_sets is not None or not excluded):
                default_members.append((group, sid))
        for code in set(group.mcp_codes) | snapshot.platform_default_codes:
            excluded = (group.id, code) in snapshot.mcp_exclusions
            if enabled and (target.skill_sets is not None or not excluded):
                default_codes.add(code)
    if enabled and not defaults:
        default_codes.update(snapshot.platform_default_codes)
    default_keys = {membership_key(snapshot, group, sid) for group, sid in default_members}

    def fail(identity, reason):
        failures.append(ResolveFailure(identity, reason))

    if target.skills is not None:
        for skill in direct:
            if owners.get(skill.key) or skill.key in default_keys:
                fail(
                    "skills/" + skill.name,
                    "APPLY_SET_SUPPLIED: Skill belongs to a SkillSet",
                )
            # Preserve the existing effective-name Set guard for a different asset.
            for group in ordinary:
                if group.is_active and any(
                    asset["name"] == skill.name
                    for asset in membership_assets(snapshot, group).values()
                ):
                    fail(
                        "skills/" + skill.name,
                        "APPLY_SET_SUPPLIED: runtime name is supplied by a SkillSet",
                    )
    target_owners, target_codes = {}, {}
    for group in groups:
        identity = "skill_sets/" + group.name
        for skill in group.skills:
            if skill.key in direct_keys:
                fail(identity, f"{skill.name}: RESOURCE_DIRECT_ACTIVE")
            elif (
                owners.get(skill.key, set()) - {group.name} or skill.key in default_keys
            ):
                fail(identity, f"{skill.name}: RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET")
            if group.is_active and skill.name in direct_names:
                fail(identity, f"{skill.name}: RESOURCE_DIRECT_ACTIVE (runtime name)")
            if skill.key in target_owners and target_owners[skill.key] != group.name:
                fail(identity, f"{skill.name}: RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET")
            target_owners[skill.key] = group.name
        for code in group.mcp:
            if code in direct_codes:
                fail(identity, f"{code}: RESOURCE_DIRECT_ACTIVE")
            elif code_owners.get(code, set()) - {group.name} or code in default_codes:
                fail(identity, f"{code}: RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET")
            if code in target_codes and target_codes[code] != group.name:
                fail(identity, f"{code}: RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET")
            target_codes[code] = group.name
    for code, _ in mcp:
        if code_owners.get(code) or code in default_codes or code in target_codes:
            fail(
                "mcp/" + code,
                "APPLY_SET_SUPPLIED: MCP belongs to a SkillSet or Default",
            )
    if target.skills is not None:
        for skill in direct:
            if skill.key in target_owners:
                fail("skills/" + skill.name, "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET")
    # Restore only real current members. Stale exclusions are cleared without inventing supply.
    if target.skill_sets is not None and enabled:
        restoring_keys = {
            membership_key(snapshot, item, i)
            for item in defaults
            for i in item.skill_ids
            if (item.id, i) in snapshot.skill_exclusions
        }
        restoring_codes = {
            code
            for item in defaults
            for code in set(item.mcp_codes) | snapshot.platform_default_codes
            if (item.id, code) in snapshot.mcp_exclusions
        }
        restoring_names = {
            asset["name"]
            for item in defaults
            for i, asset in membership_assets(snapshot, item).items()
            if (item.id, i) in snapshot.skill_exclusions
        }
        if restoring_names & set(direct_names):
            fail(
                "skill_sets/default",
                "Default restoration conflicts with a current Direct runtime name",
            )
        if restoring_keys & (
            direct_keys | set(owners) | set(target_owners)
        ) or restoring_codes & (
            set(direct_codes) | set(code_owners) | set(target_codes)
        ):
            fail(
                "skill_sets/default",
                "Default restoration conflicts with Direct or ordinary SkillSet ownership",
            )
    final = []
    origins = {}
    final_groups = list(groups)
    if target.skill_sets is None:
        for group in ordinary:
            # Inactive memberships still own their assets above, but supply no content.
            if not group.is_active:
                continue
            try:
                final_groups.append(
                    SkillSet(
                        group.name,
                        group.description,
                        group.is_active,
                        tuple(existing_member(snapshot, group, i) for i in group.skill_ids),
                        group.mcp_codes,
                    )
                )
            except ValueError as exc:
                fail("skill_sets/" + group.name, str(exc))
    for group, sid in default_members:
        try:
            found = existing_member(snapshot, group, sid)
            final.append(found)
            origins.setdefault(found.key, set()).add("skill_sets/default")
        except ValueError as exc:
            fail("skill_sets/default", str(exc))
    for group in final_groups:
        if group.is_active:
            for skill in group.skills:
                final.append(skill)
                origins.setdefault(skill.key, set()).add("skill_sets/" + group.name)
    final_codes = default_codes | {
        code for group in final_groups if group.is_active for code in group.mcp
    }
    if target.skills is None:
        for sid in current_direct:
            try:
                found = existing_skill(snapshot, sid)
                final.append(found)
                origins.setdefault(found.key, set()).add("skills/" + found.name)
            except ValueError as exc:
                fail("skills/" + catalog.get(sid, {}).get("name", str(sid)), str(exc))
    else:
        final.extend(direct)
        for skill in direct:
            origins.setdefault(skill.key, set()).add("skills/" + skill.name)
    declared_rows = {"skills/" + skill.name for skill in direct} | {
        "skill_sets/" + group.name for group in groups
    }
    if target.skill_sets is not None and enabled:
        declared_rows.add("skill_sets/default")
    names = {}
    for skill in final:
        if skill.name in names and names[skill.name] != skill.key:
            affected = origins[skill.key] | origins[names[skill.name]]
            rows = affected & declared_rows or affected
            for identity in sorted(rows):
                fail(
                    identity,
                    f"{skill.name}: different effective Skill assets share the same runtime name",
                )
        names[skill.name] = skill.key
    resolved = Resolved(
        target,
        snapshot,
        tuple(direct),
        tuple(groups),
        change,
        enabled,
        tuple(final),
        frozenset(final_codes),
        tuple(mcp),
    )
    # Keep one stable failure per report row; member details remain in the reason.
    combined = {}
    for failure in failures:
        combined.setdefault(failure.identity, []).append(failure.reason)
    return resolved, tuple(
        ResolveFailure(k, "; ".join(dict.fromkeys(v))) for k, v in combined.items()
    )
