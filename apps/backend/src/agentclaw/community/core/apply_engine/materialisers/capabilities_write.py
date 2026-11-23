"""Ordered writes; durable Bot work is tracked using the existing partial-error contract."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack

from agentclaw.community.core.apply_engine.capabilities import Category
from agentclaw.community.core.apply_engine.outcomes import EntryOutcome, EntryResult
from agentclaw.community.core.apply_engine.registry import (
    CapabilityWriteError,
    CategoryWriteResult,
    ConfirmedPartialWriteError,
)
from agentclaw.community.core.devices.services.device_context import expect_runtime_binding
from agentclaw.community.core.ports.skill_package_upload_port import (
    BatchSkillPackageUploadPort,
    LocalSkillPackageReceipt,
    PackagePublication,
)
from agentclaw.community.core.skill_center.errors import ApplyDesiredStateCommittedError
from agentclaw.community.core.skill_center.skill_set_apply import ResolvedSkillSetWrite
from .capabilities_skills import access_scope
from .capabilities_types import (
    assets,
    center_key,
    direct_ids,
    effective_sets,
    existing_skill,
    local_owned,
)


def _projection_note(result):
    if not isinstance(result, dict):
        return None
    projection = result.get("runtime_projection", {})
    status = (
        str(projection.get("status", "")).upper()
        if isinstance(projection, dict)
        else ""
    )
    if status in ("PENDING", "DEGRADED"):
        return "Runtime projection is " + status.lower()
    return None


def _confirmed_mutation(result):
    """A completed command can be a no-op; unknown receipts prove no change."""
    return isinstance(result, dict) and result.get("changed") is True


def _claim_outcome(result):
    """Only a command's transactional facts describe its normal outcome."""
    if not isinstance(result, dict):
        raise ValueError("Apply claim did not return a mutation receipt")
    if "outcome" in result:
        return EntryOutcome(result["outcome"])
    if result.get("changed") is False:
        return EntryOutcome.UNCHANGED
    raise ValueError("Apply claim did not confirm its outcome")


def _combined_outcome(*outcomes):
    return max(outcomes, key=lambda value: ("unchanged", "created", "updated").index(value))


async def write_capabilities(owner, ctx, plan):
    resolved = plan.resolved
    snapshot = resolved.snapshot
    who = dict(bot_id=ctx.bot_id, owner_id=ctx.entity_id, actor_id=ctx.actor_id)
    confirmed = False
    results = {
        e.intent.identity: EntryResult(
            Category.CAPABILITIES,
            e.intent.identity,
            EntryOutcome(e.outcome),
            note=e.intent.note,
        )
        for e in plan.entries
    }
    removals = set()
    pending_asset_cleanup = set()
    completed = set()
    prepared = {}
    uploaded = {}
    package_receipts: tuple[LocalSkillPackageReceipt, ...] = ()
    scope = access_scope(ctx)
    all_skills = tuple(resolved.direct) + tuple(
        s for group in resolved.sets for s in group.skills
    )
    binding_scope = ExitStack()
    try:
        # Complete every shared publication before the first Bot Local write.
        for skill in all_skills:
            if skill.key in prepared:
                continue
            if skill.preview is not None:
                ready = await asyncio.to_thread(
                    owner._references.prepare_public_reference,
                    scope=scope,
                    preview=skill.preview,
                )
                if (
                    ready.name != skill.name
                    or ready.mcp_dependencies != skill.dependencies
                    or center_key(ready.skill_uuid) != skill.key
                ):
                    raise ValueError(
                        "public Skill changed name, identity or dependencies after preview; shared publication may remain"
                    )
                prepared[skill.key] = ready.skill_id
            elif skill.from_bot is not None:
                # The dst ``ac_skill`` row ``resolve`` deferred for a
                # ``from: from_bot`` local skill — file-sync owns the bytes,
                # so no package is uploaded. Register the row now
                # (create-or-replace under the edit guard, the upload road's
                # contract), and let the returned id drive the activation
                # claim below. ``resolve`` left this here precisely so a
                # ``dry_run`` (which stops before ``write``) writes nothing.
                result = await owner._uploads.ensure_dst_local_skill_asset(
                    bot_id=ctx.bot_id,
                    owner_id=ctx.owner_id,
                    actor_id=ctx.actor_id,
                    source_bot_id=skill.from_bot.source_bot_id,
                    source_owner_id=skill.from_bot.source_owner_id,
                    name=skill.name,
                )
                confirmed = True
                operation = result["operation"]
                uploaded[skill.key] = EntryOutcome(
                    "updated" if operation == "replaced" else operation
                )
                prepared[skill.key] = str(result["skill"]["id"])
            elif skill.package is None:
                prepared[skill.key] = skill.skill_id
        local_packages = {
            skill.key: (skill.name, skill.package)
            for skill in all_skills
            if skill.package is not None and skill.key not in prepared
        }
        if local_packages and ctx.defer_capability_projection:
            if not isinstance(owner._uploads, BatchSkillPackageUploadPort):
                raise TypeError("This capability delivery requires a device Local Skill batch port")
            batch = await owner._uploads.upload_local_skill_batch(
                **who, packages=tuple(local_packages.values()),
            )
            package_receipts = tuple(item.receipt for item in batch)
            confirmed = any(
                receipt.publication is PackagePublication.PUBLISHED
                for receipt in package_receipts
            )
            if any(item.upload is None for item in batch):
                raise CapabilityWriteError(
                    "Local Skill package batch did not finish; later capability writes were skipped",
                    local_skill_packages=package_receipts, confirmed_partial=confirmed,
                )
            uploads = [item.upload for item in batch]
            if batch and batch[0].target is not None:
                binding_scope.enter_context(expect_runtime_binding(batch[0].target))
        else:
            uploads = []
            for _, package in local_packages.values():
                uploads.append(await owner._uploads.upload_local_skill(**who, package=package))
                confirmed = True
        for key, upload in zip(local_packages, uploads, strict=True):
            assert upload is not None
            operation = upload["operation"]
            uploaded[key] = EntryOutcome(
                "updated" if operation == "replaced" else operation
            )
            prepared[key] = str(upload["skill"]["id"])
        if plan.set_write:
            result = await owner._sets.replace_apply_skill_sets(
                scope=snapshot.scope,
                actor_id=ctx.actor_id,
                sets=tuple(
                    ResolvedSkillSetWrite(
                        group.name,
                        group.description,
                        group.is_active,
                        tuple(int(prepared[s.key]) for s in group.skills),
                        group.mcp,
                    )
                    for group in resolved.sets
                ),
                remove_names=plan.remove_sets,
                default_change=resolved.default_change,
                platform_default_codes=snapshot.platform_default_codes,
            )
            confirmed = confirmed or result.changed
            removals.update("skill_sets/" + n for n in result.removed)
            member_uploads = {
                group.name: tuple(uploaded[s.key] for s in group.skills if s.key in uploaded)
                for group in resolved.sets
            }
            for outcome, names in (
                ("created", result.created),
                ("updated", result.updated),
                ("unchanged", result.unchanged),
            ):
                for name in names:
                    identity = "skill_sets/" + name
                    results[identity] = EntryResult(
                        Category.CAPABILITIES,
                        identity,
                        _combined_outcome(EntryOutcome(outcome), *member_uploads[name]),
                        note=_projection_note(
                            {"runtime_projection": result.runtime_projection}
                        ),
                    )
                    completed.add(identity)
            if result.default_removed:
                removals.add("skill_sets/default")
            if resolved.default_enabled and "skill_sets/default" in results:
                results["skill_sets/default"] = EntryResult(
                    Category.CAPABILITIES,
                    "skill_sets/default",
                    EntryOutcome.UPDATED
                    if result.default_changed
                    else EntryOutcome.UNCHANGED,
                    note=_projection_note(
                        {"runtime_projection": result.runtime_projection}
                    ),
                )
                completed.add("skill_sets/default")
        for skill in resolved.direct:
            identity = "skills/" + skill.name
            entry = next(e for e in plan.entries if e.intent.identity == identity)
            if not entry.requires_write:
                continue
            result = await owner._activation.claim_apply_skill(
                **who, skill_id=prepared[skill.key], apply_id=ctx.apply_id
            )
            confirmed = confirmed or _confirmed_mutation(result)
            note = _projection_note(result) or skill.note
            outcome = _claim_outcome(result)
            upload_outcome = uploaded.get(skill.key)
            if upload_outcome is not None:
                outcome = _combined_outcome(outcome, upload_outcome)
            results[identity] = EntryResult(
                Category.CAPABILITIES, identity, outcome, note=note
            )
            completed.add(identity)
        # Names, not stale IDs, define Direct removal scope too.
        actual = await asyncio.to_thread(owner._snapshot, ctx)
        catalog = assets(actual)
        for sid in sorted(direct_ids(actual)):
            asset = catalog.get(sid)
            if asset is None or asset["name"] not in plan.remove_skills:
                continue
            result = await owner._activation.remove_apply_skill(
                **who, skill_id=str(asset["id"]), apply_id=ctx.apply_id
            )
            if _confirmed_mutation(result):
                confirmed = True
                removals.add("skills/" + asset["name"])
                if sid in plan.cleanup_ids:
                    pending_asset_cleanup.add("skills/" + asset["name"])
        # Re-observe actual effective supply after all Skill commands. No predicted context handoff.
        actual = await asyncio.to_thread(owner._snapshot, ctx)
        set_ids, supplied_mcps = effective_sets(actual)
        effective_ids = set(actual.installed_skill_ids) | set_ids
        dependencies = set()
        for sid in effective_ids:
            dependencies.update(existing_skill(actual, sid).dependencies)
        if resolved.target.mcp is not None:
            direct_codes = actual.installed_mcp_codes - supplied_mcps
            overrides = {
                code for code in actual.mcp_overrides if code not in supplied_mcps
            }
            declared = {code for code, _ in resolved.mcp}
            for code, config in resolved.mcp:
                # The command rechecks scope/permissions and consumes legacy aliases.
                entry = next(
                    e for e in plan.entries if e.intent.identity == "mcp/" + code
                )
                if not entry.requires_write:
                    continue
                result = await owner._activation.claim_apply_mcp(
                    **who, server_code=code, config=config, apply_id=ctx.apply_id
                )
                confirmed = confirmed or _confirmed_mutation(result)
                results["mcp/" + code] = EntryResult(
                    Category.CAPABILITIES,
                    "mcp/" + code,
                    _claim_outcome(result),
                    note=_projection_note(result),
                )
                completed.add("mcp/" + code)
            candidates = (
                (set(direct_codes) | overrides | set(plan.remove_mcps))
                - supplied_mcps
                - declared
            )
            for code in sorted(candidates):
                result = await owner._activation.remove_apply_mcp(
                    **who, server_code=code, apply_id=ctx.apply_id
                )
                if not _confirmed_mutation(result):
                    identity = "mcp/" + code
                    if identity in results:
                        results[identity] = EntryResult(
                            Category.CAPABILITIES, identity, EntryOutcome.UNCHANGED
                        )
                    continue
                confirmed = True
                identity = "mcp/" + code
                if code in dependencies:
                    results[identity] = EntryResult(
                        Category.CAPABILITIES,
                        identity,
                        EntryOutcome.UPDATED,
                        note="explicit MCP supply and Bot override are removed; the server remains available as a final Skill dependency",
                    )
                    completed.add(identity)
                else:
                    removals.add(identity)
                    results.pop(identity, None)
        # Cleanup is authorized only by a participating Direct Skills Replace.
        if resolved.target.skills is not None and plan.cleanup_ids:
            actual = await asyncio.to_thread(owner._snapshot, ctx)
            target_ids = {
                int(value) for value in prepared.values() if value is not None
            }
            for asset in actual.skills:
                sid = int(asset["id"])
                if (
                    sid not in plan.cleanup_ids
                    or sid in target_ids
                    or sid in actual.raw_member_skill_ids
                    or sid in actual.installed_skill_ids
                    or not local_owned(asset, actual)
                ):
                    continue
                await owner._uploads.delete_local_skill(
                    **who, skill_id=str(sid), name=asset["name"]
                )
                pending_asset_cleanup.discard("skills/" + asset["name"])
                confirmed = True
                if asset["name"] not in {skill.name for skill in resolved.final_skills}:
                    removals.add("skills/" + asset["name"])
    except Exception as exc:
        if isinstance(exc, (ConfirmedPartialWriteError, CapabilityWriteError)):
            raise
        if package_receipts or confirmed or isinstance(exc, ApplyDesiredStateCommittedError):
            raise CapabilityWriteError(
                "Capability replacement stopped after starting Bot writes",
                local_skill_packages=package_receipts,
                confirmed_partial=confirmed or isinstance(exc, ApplyDesiredStateCommittedError),
                entries=tuple(results[key] for key in sorted(completed)),
                removals=tuple(sorted(removals - pending_asset_cleanup)),
            ) from None
        # No Bot write is confirmed: an earlier shared publication may still remain.
        raise RuntimeError(
            "Capability write failed; shared preparation may remain, Bot writes are not confirmed"
        ) from exc
    finally:
        binding_scope.close()
    ordered = tuple(
        results[e.intent.identity] for e in plan.entries if e.intent.identity in results
    )
    extra = tuple(
        results[k]
        for k in sorted(set(results) - {e.intent.identity for e in plan.entries})
    )
    return CategoryWriteResult(ordered + extra, tuple(sorted(removals)), package_receipts)
