"""Capability target grammar, sharing the existing entry and source validators."""

from typing import Any
from agentclaw.community.core.apply_engine.capabilities import Category
from agentclaw.community.core.bot_config_manifest.schema._support import (
    Context,
    check_placeholders,
    duplicates,
)
from agentclaw.community.core.bot_config_manifest.schema.entries import (
    validate_skill_entry,
    validate_mcp_entry,
)
from agentclaw.community.core.bot_config_manifest.schema.limits import (
    MAX_ENTRIES_PER_CATEGORY,
)

CAPABILITY_KEYS = frozenset({"skills", "skill_sets", "mcp"})


def capability_input(manifest: dict[str, Any]) -> dict[str, Any]:
    """Read a validated input without mutating the digest-bound original."""
    return manifest["capabilities"] if "capabilities" in manifest else manifest


def validate_capabilities(ctx: Context, manifest: dict[str, Any]) -> None:
    if "capabilities" in manifest:
        if CAPABILITY_KEYS.intersection(manifest):
            ctx.add(
                "manifest.capabilities",
                "mixed_capabilities",
                "capabilities cannot be mixed with flat skills, skill_sets or mcp",
            )
        target = manifest["capabilities"]
        if not isinstance(target, dict):
            ctx.add(
                "manifest.capabilities",
                "invalid_capabilities",
                "capabilities must be a mapping",
            )
            return
        for key in target.keys() - CAPABILITY_KEYS:
            ctx.add(
                f"manifest.capabilities.{key}",
                "unknown_field",
                f"unknown capability field {key!r}",
            )
        base = "manifest.capabilities"
    else:
        target = manifest
        base = "manifest"
    explicit = CAPABILITY_KEYS.intersection(target)
    if not explicit and "capabilities" not in manifest:
        return
    if not ctx.capabilities.supports(Category.CAPABILITIES):
        ctx.add(
            base,
            "unsupported_category",
            ctx.capabilities.reason_for(Category.CAPABILITIES),
        )
    skills = _members(ctx, f"{base}.skills", target.get("skills", []), skills=True)
    mcps = _members(ctx, f"{base}.mcp", target.get("mcp", []), skills=False)
    sets = target.get("skill_sets", [])
    if not isinstance(sets, list):
        ctx.add(f"{base}.skill_sets", "invalid_category", "skill_sets must be a list")
        return
    ordinary = 0
    names = []
    for index, entry in enumerate(sets):
        at = f"{base}.skill_sets[{index}]"
        if not isinstance(entry, dict):
            ctx.add(at, "invalid_entry", "a SkillSet entry must be a mapping")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            ctx.add(f"{at}.name", "invalid_name", "a SkillSet needs a non-empty name")
        else:
            names.append(name)
        if name == "default":
            _keys(ctx, at, entry, {"name"})
            continue
        ordinary += 1
        _keys(ctx, at, entry, {"name", "description", "is_active", "skills", "mcp"})
        for key in ("name", "is_active", "skills", "mcp"):
            if key not in entry:
                ctx.add(
                    f"{at}.{key}",
                    "missing_field",
                    f"{key} is required for a regular SkillSet",
                )
        if type(entry.get("is_active")) is not bool:
            ctx.add(f"{at}.is_active", "invalid_entry", "is_active must be a boolean")
        if "description" in entry and not isinstance(entry["description"], str):
            ctx.add(
                f"{at}.description", "invalid_entry", "description must be a string"
            )
        skills += _members(ctx, f"{at}.skills", entry.get("skills"), skills=True)
        mcps += _members(ctx, f"{at}.mcp", entry.get("mcp"), skills=False, member=True)
    for name in duplicates(names):
        ctx.add(
            f"{base}.skill_sets", "duplicate_name", f"duplicate SkillSet name {name!r}"
        )
    for label, count in (("skill_sets", ordinary), ("skills", skills), ("mcp", mcps)):
        if count > MAX_ENTRIES_PER_CATEGORY:
            ctx.add(
                f"{base}.{label}",
                "too_many_entries",
                f"{count} declarations exceed the shared {MAX_ENTRIES_PER_CATEGORY}-entry limit",
            )


def _keys(ctx: Context, at: str, entry: dict[str, Any], allowed: set[str]) -> None:
    for key in entry.keys() - allowed:
        if key != "apply_once":
            ctx.add(f"{at}.{key}", "unknown_field", f"unknown field {key!r}")


def _members(
    ctx: Context, at: str, entries: Any, *, skills: bool, member: bool = False
) -> int:
    if not isinstance(entries, list):
        ctx.add(at, "invalid_category", "members must be a list")
        return 0
    names = []
    for index, entry in enumerate(entries):
        location = f"{at}[{index}]"
        if not isinstance(entry, dict):
            ctx.add(location, "invalid_entry", "a member must be a mapping")
            continue
        _placeholders(ctx, location, entry)
        if skills:
            name = validate_skill_entry(ctx, location, entry)
        else:
            if member:
                _keys(ctx, location, entry, {"server_code"})
            validate_mcp_entry(ctx, location, entry)
            name = entry.get("server_code")
        if isinstance(name, str):
            names.append(name)
    for name in duplicates(names):
        ctx.add(
            at,
            "duplicate_name" if skills else "duplicate_server_code",
            f"duplicate member {name!r}",
        )
    return len(entries)


def _placeholders(ctx: Context, location: str, entry: Any) -> None:
    if isinstance(entry, dict):
        for key, value in entry.items():
            if key != "content":
                _placeholders(ctx, f"{location}.{key}", value)
    elif isinstance(entry, list):
        for index, value in enumerate(entry):
            _placeholders(ctx, f"{location}[{index}]", value)
    else:
        check_placeholders(ctx, location, entry)
