"""Pure comparison of a resolved capability target with its read-only snapshot."""

from __future__ import annotations

from agentclaw.community.core.apply_engine.registry import Intent, PlannedEntry
from agentclaw.community.core.mcp.config_comparison import comparable_config
from .capabilities_types import (
    CapabilityPlan,
    Resolved,
    asset_key,
    assets,
    direct_ids,
    effective_sets,
    local_owned,
    membership_assets,
)


def build_plan(resolved: Resolved) -> CapabilityPlan:
    target, snapshot = resolved.target, resolved.snapshot
    catalog = assets(snapshot)
    current_direct = {
        catalog[i]["name"]: catalog[i] for i in direct_ids(snapshot) if i in catalog
    }
    effective_ids, old_set_codes = effective_sets(snapshot)
    all_current_ids = set(snapshot.installed_skill_ids) | effective_ids
    entries, removals = [], []
    cleanup = set()
    remove_skills = ()
    if target.skills is not None:
        declared = {s.name for s in resolved.direct}
        for skill in resolved.direct:
            current = current_direct.get(skill.name)
            same = current is not None and asset_key(current) == skill.key
            effective = any(
                catalog[i]["name"] == skill.name and asset_key(catalog[i]) == skill.key
                for i in all_current_ids
                if i in catalog
            )
            if skill.package is not None:
                outcome = "updated" if current is not None or effective else "created"
            elif current is not None and not same:
                outcome = "updated"
            else:
                outcome = "unchanged" if effective else "created"
            entries.append(
                PlannedEntry(
                    Intent("skills/" + skill.name, skill, skill.note),
                    outcome,
                    # Installation equality does not establish runtime convergence.
                    # Always reach the claim flow; delivery delegates decide projection.
                    requires_write=True,
                )
            )
        remove_skills = tuple(sorted(set(current_direct) - declared))
        removals.extend("skills/" + name for name in remove_skills)
        protected = {s.key for s in resolved.direct} | {
            s.key for group in resolved.sets for s in group.skills
        }
        replaced_set_ids = (
            {group.id for group in snapshot.sets if not group.is_default}
            if target.skill_sets is not None
            else set()
        )
        for asset in catalog.values():
            if local_owned(asset, snapshot) and asset_key(asset) not in protected:
                cleanup.add(int(asset["id"]))
                sid = int(asset["id"])
                references = snapshot.local_skill_set_references.get(sid)
                # Older in-process fixtures can lack detailed facts; retain their protection.
                retained_reference = (
                    (sid in snapshot.raw_member_skill_ids)
                    if references is None
                    else bool(references - replaced_set_ids)
                )
                if asset["name"] not in declared and not retained_reference:
                    removals.append("skills/" + asset["name"])
    remove_sets = ()
    set_write = False
    change = resolved.default_change
    default_changed = any(
        (change.add_skills, change.remove_skills, change.add_mcps, change.remove_mcps)
    )
    default_removed = bool(change.add_skills or change.add_mcps)
    if target.skill_sets is not None:
        existing = {s.name: s for s in snapshot.sets if not s.is_default}
        remove_sets = tuple(sorted(set(existing) - {s.name for s in resolved.sets}))
        removals.extend("skill_sets/" + name for name in remove_sets)
        if resolved.default_enabled:
            # Default read may need to repair absent Installation without changing exclusions.
            defaults = [
                group for group in snapshot.sets if group.is_default and group.is_active
            ]
            ids = {
                int(asset["id"])
                for group in defaults
                for asset in membership_assets(snapshot, group).values()
            }
            codes = {
                code
                for group in defaults
                for code in set(group.mcp_codes) | snapshot.platform_default_codes
            }
            missing = bool(
                ids - snapshot.installed_skill_ids
                or codes - snapshot.installed_mcp_codes
            )
            entries.append(
                PlannedEntry(
                    Intent("skill_sets/default"),
                    "updated" if default_changed or missing else "unchanged",
                    requires_write=default_changed or missing,
                )
            )
        elif default_removed:
            removals.append("skill_sets/default")
        for group in resolved.sets:
            current = existing.get(group.name)
            current_keys = (
                {asset_key(asset) for asset in membership_assets(snapshot, current).values()}
                if current
                else set()
            )
            same = current is not None and (
                current.description == group.description
                and current.is_active == group.is_active
                and current_keys == {s.key for s in group.skills}
                and len(current_keys) == len(current.skill_ids)
                and set(current.mcp_codes) == set(group.mcp)
            )
            outcome = "unchanged" if same else ("updated" if current else "created")
            # Local content replacement remains a durable operation despite equal membership.
            missing_installation = group.is_active and (
                any(
                    s.skill_id is not None
                    and int(s.skill_id) not in snapshot.installed_skill_ids
                    for s in group.skills
                )
                or bool(set(group.mcp) - snapshot.installed_mcp_codes)
            )
            required = (
                not same
                or missing_installation
                or any(
                    s.package is not None or s.preview is not None for s in group.skills
                )
            )
            entries.append(
                PlannedEntry(
                    Intent("skill_sets/" + group.name, group),
                    outcome,
                    requires_write=required,
                )
            )
        set_write = (
            default_changed
            or bool(remove_sets)
            or any(
                e.requires_write
                for e in entries
                if e.intent.identity.startswith("skill_sets/")
            )
        )
    dependencies = (
        set().union(*(s.dependencies for s in resolved.final_skills))
        if resolved.final_skills
        else set()
    )
    remove_mcps = ()
    if target.mcp is not None:
        direct = set(snapshot.installed_mcp_codes) - old_set_codes
        overrides = {
            code: value
            for code, value in snapshot.mcp_overrides.items()
            if code not in resolved.set_codes
        }
        declared = {code for code, _ in resolved.mcp}
        for code, config in resolved.mcp:
            current = code in direct or code in dependencies or code in old_set_codes
            same = comparable_config(overrides.get(code)) == comparable_config(config)
            outcome = "created" if not current else ("unchanged" if same else "updated")
            entries.append(
                PlannedEntry(
                    Intent("mcp/" + code, config),
                    outcome,
                    # Effective equality does not reveal safe legacy aliases.
                    # The command consumes them atomically; canonical equality
                    # remains a repository no-op after convergence.
                    requires_write=True,
                )
            )
        # A Set that just withdrew no longer protects its persistent Bot override.
        candidates = (direct | set(overrides)) - set(resolved.set_codes)
        remove_mcps = tuple(sorted(candidates - declared))
        for code in remove_mcps:
            if code in dependencies:
                entries.append(
                    PlannedEntry(
                        Intent(
                            "mcp/" + code,
                            note="explicit MCP supply and Bot override are removed; the server remains available as a final Skill dependency",
                        ),
                        "updated",
                        requires_write=True,
                    )
                )
            else:
                removals.append("mcp/" + code)
    removal_writes = set(removals) | {"mcp/" + code for code in remove_mcps}
    # Empty targets with exclusion-only effects still need write even without rows.
    if set_write and not removal_writes and not any(e.requires_write for e in entries):
        removal_writes.add("skill_sets/default")
    return CapabilityPlan(
        entries=tuple(entries),
        removals=tuple(sorted(set(removals))),
        removal_writes=tuple(sorted(removal_writes)),
        resolved=resolved,
        remove_sets=remove_sets,
        remove_skills=remove_skills,
        cleanup_ids=frozenset(cleanup),
        remove_mcps=remove_mcps,
        set_write=set_write,
    )
