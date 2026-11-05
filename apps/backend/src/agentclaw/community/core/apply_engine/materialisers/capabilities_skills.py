"""Shared source resolution for Direct and ordinary SkillSet members."""

from __future__ import annotations

import asyncio

from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillByCode,
    CenterSkillRef,
    LocalSkillPackage,
    RepoSkillPath,
)
from agentclaw.community.core.apply_engine.skill_validation import validate_package
from agentclaw.community.core.content_store import decode_tree_bytes
from agentclaw.community.core.skill_center.skill_reference_contract import (
    PublicSkillPreview,
    ReadySharedSkill,
    SkillAccessScope,
)
from .capabilities_types import Skill, asset_key, center_key, local_owned


def access_scope(ctx):
    return SkillAccessScope(ctx.env, ctx.bot_id, ctx.entity_id, ctx.actor_id)


async def resolve_skill(materialiser, ctx, snapshot, entry):
    source = entry.source
    scope = access_scope(ctx)
    if isinstance(source, LocalSkillPackage):
        raw = await asyncio.to_thread(materialiser._content.read, source.tree.digest)
        files = decode_tree_bytes(raw)
        if files is None:
            raise ValueError("the stored skill package is not an acm-tree-v1 tree")
        package = await asyncio.to_thread(
            validate_package, materialiser._validator.validate_directory, files
        )
        if package.name != entry.name:
            raise ValueError(
                f"the package names its skill {package.name!r}, but the entry declares {entry.name!r}"
            )
        matches = [
            a
            for a in snapshot.skills
            if a["name"] == entry.name and local_owned(a, snapshot)
        ]
        if len(matches) > 1:
            raise ValueError("multiple Bot Local assets have this runtime name")
        existing = matches[0] if matches else None
        if existing and existing.get("resolution_error"):
            raise ValueError(str(existing["resolution_error"]))
        return Skill(
            entry.name,
            asset_key(existing) if existing else "local:" + entry.name,
            str(existing["id"]) if existing else None,
            frozenset(existing["mcp_codes"]) if existing else frozenset(),
            package=package.canonical_zip,
            note=entry.note,
        )
    if isinstance(source, CenterSkillRef):
        ready = await asyncio.to_thread(
            materialiser._references.resolve_center_uuid,
            scope=scope,
            skill_uuid=source.skill_uuid,
            expected_name=entry.name,
        )
    elif isinstance(source, CenterSkillByCode):
        # Import only at use: the budget leaf is shared with compiler execution.
        from agentclaw.community.core.apply_engine.fetch_budget import (
            current_fetch_budget,
        )

        budget = current_fetch_budget()
        if budget is not None and (reason := budget.expired()):
            raise ValueError(reason)
        ready = await asyncio.to_thread(
            materialiser._references.preview_public_reference,
            scope=scope,
            skill_code=source.skill_code,
            expected_name=entry.name,
        )
        if isinstance(ready, PublicSkillPreview) and budget is not None:
            if reason := budget.expired():
                raise ValueError(reason)
    else:
        who = dict(
            bot_id=ctx.bot_id,
            owner_id=ctx.entity_id,
            actor_id=ctx.actor_id,
            name=entry.name,
        )
        if isinstance(source, RepoSkillPath):
            skill_id = await asyncio.to_thread(
                materialiser._shared.find_repo_skill, **who, path=source.path
            )
        else:
            skill_id = await asyncio.to_thread(
                materialiser._shared.find_center_skill, **who
            )
        ready = await asyncio.to_thread(
            materialiser._references.resolve_shared_skill_id,
            scope=scope,
            skill_id=skill_id,
            expected_name=entry.name,
        )
    dependencies = ready.mcp_dependencies
    if ready.name != entry.name:
        raise ValueError("resolved Skill runtime name differs from the declaration")
    if isinstance(ready, PublicSkillPreview):
        return Skill(
            ready.name, center_key(ready.skill_uuid), None, dependencies, preview=ready
        )
    key = (
        center_key(ready.skill_uuid)
        if isinstance(ready, ReadySharedSkill)
        else "id:" + ready.skill_id
    )
    return Skill(ready.name, key, ready.skill_id, dependencies)
