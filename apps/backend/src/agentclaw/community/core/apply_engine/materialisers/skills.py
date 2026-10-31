"""Materialise the desired skills as the bot's complete Direct Skill and Bot-owned Local snapshot.

The declaration speaks only for Direct skills: a runtime name the bot's skill
sets supply (an active ordinary set, the Default less its exclusions) is
outside it — never counted as current, never removed. Declaring such a name is
a conflict (``set_conflicts``), which refuses the whole apply before anything
is written, so a claim never has to touch a set.

Every declared local package is validated and fully written through the normal
Local package road, then claimed Direct. Existing Direct names report
``updated`` and absent names report ``created``—package equality is
deliberately not probed.

A declared Skill Center skill (by its runtime name) or shared-repository
skill (by its path) is found through ``SharedSkillPort`` — the Skill Center's
own visibility rule, for the bot and the apply's principal — and the shared
asset itself becomes the Direct claim; it is never copied into bot-owned
storage. None found, or several Center skills of one name, fails that entry
with the reason, which aborts the category the way any resolve failure does.

When the section is present, every omitted Direct Skill is removed and every
omitted Bot-owned Local asset no set references (an inactive set included) is
additionally physically deleted and removed from the catalog. Shared Repo/Center assets are never physically
deleted. The complete Local catalog, not only active Installations, makes failed
cleanup discoverable on a later Apply.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Sequence

from agentclaw.community.core.content_store import ContentReader
from agentclaw.community.core.apply_engine.skill_validation import PackageRefusal, validate_package
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.desired_state import DesiredSkill, LocalSkillPackage, RepoSkillPath
from agentclaw.community.core.content_store import decode_tree_bytes
from agentclaw.community.core.content_store.errors import ContentStoreError
from agentclaw.community.core.apply_engine.outcomes import (
    EntryOutcome,
    EntryResult,
)
from agentclaw.community.core.apply_engine.registry import (
    CategoryPlan,
    ConfirmedPartialWriteError,
    Intent,
    ChecksSetSupply,
    Materialiser,
    PlannedEntry,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.apply_engine.capabilities import (
    Category,
)
from agentclaw.community.core.ports.activation_port import ActivationPort
from agentclaw.community.core.ports.shared_skill_port import (
    SharedSkillAmbiguous,
    SharedSkillNotFound,
    SharedSkillPort,
)
from agentclaw.community.core.ports.skill_package_upload_port import (
    SkillPackageUploadPort,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    ApplyDesiredStateCommittedError,
)
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageValidator,
)
from agentclaw.community.log import get_logger

logger = get_logger()

@dataclass(frozen=True)
class _SharedSkill:
    """A shared Center or repository asset the bot may use, found by the lookup: the intent's value."""

    skill_id: str


class _SkillPackage:
    """One entry's validated, upload-ready package — the intent's value.

    Carried as ``Intent.value``, so a planned entry reads
    ``planned.intent.value.canonical_zip``::

        _SkillPackage(
            name="quality-check",          # from the package's SKILL.md
            canonical_zip=b"PK\x03\x04...",  # what upload_local_skill takes
            note=None,
        )

    On a ``keep_last`` fallback the compiler's note rides along, and the
    write puts it on the entry's report row.

    Created by: :meth:`SkillsMaterialiser.resolve`.
    Consumed by: that materialiser's ``plan`` and ``write``.
    """

    __slots__ = ("name", "canonical_zip", "note")

    def __init__(self, name: str, canonical_zip: bytes, *, note: "str | None" = None) -> None:
        self.name = name
        self.canonical_zip = canonical_zip
        # The compiler's note for the entry (a keep_last fallback's reason),
        # surfaced on the report row — see Intent.note for why it travels
        # inside the value.
        self.note = note


def _without(assets: dict[str, Any], supplied: frozenset[str]) -> dict[str, Any]:
    """The assets by name, less the names a skill set supplies: the Direct ones."""
    return {name: asset for name, asset in assets.items() if name not in supplied}


class SkillsMaterialiser(Materialiser, ChecksSetSupply):
    """Converges the bot's active skills toward the declared package set.

    ``identity`` is the entry's ``name``, e.g. ``"quality-check"``, and
    ``Intent.value`` is a :class:`_SkillPackage`. The three stages, for one
    entry naming a skill the bot does not yet have::

        resolve -> ResolveResult(intents=(Intent(
                       identity="quality-check",
                       value=_SkillPackage("quality-check", b"PK...", ...)),))
        plan    -> CategoryPlan(
                       entries=(PlannedEntry(<that intent>, "created"),),
                       removals=("retired-skill",))
        write   -> (EntryResult(Category.SKILLS, "quality-check",
                                EntryOutcome.CREATED),)
    """

    construct = Category.SKILLS

    def __init__(
        self,
        upload_service: SkillPackageUploadPort,
        activation_service: ActivationPort,
        capability_reader: BotCapabilityStateReaderProtocol,
        validator: SkillPackageValidator,
        content: ContentReader,
        shared_skills: SharedSkillPort,
    ) -> None:
        self._uploads = upload_service
        self._activation = activation_service
        self._reader = capability_reader
        self._validator = validator
        self._content = content
        self._shared = shared_skills

    async def resolve(self, ctx: ApplyContext, entries: Sequence[DesiredSkill]) -> ResolveResult:
        """Desired entries → validated packages, every refusal up front.

        A local package's stored tree goes through ``validate_directory`` —
        the same validator the manual upload service runs, so limits and
        layout stay one rule — and its canonical zip is what the upload takes.
        A Skill Center or shared-repository skill resolves to the shared
        asset's id (see the module docstring), or to a failure naming why not.
        """
        intents: list[Intent] = []
        failures: list[ResolveFailure] = []
        for entry in entries:
            name = entry.name
            if not isinstance(entry.source, LocalSkillPackage):
                who = dict(bot_id=ctx.bot_id, owner_id=ctx.entity_id, actor_id=ctx.actor_id)
                try:
                    if isinstance(entry.source, RepoSkillPath):
                        skill_id = self._shared.find_repo_skill(**who, name=name, path=entry.source.path)
                    else:
                        skill_id = self._shared.find_center_skill(**who, name=name)
                except (SharedSkillNotFound, SharedSkillAmbiguous) as exc:
                    failures.append(ResolveFailure(name, str(exc)))
                    continue
                intents.append(Intent(name, _SharedSkill(skill_id)))
                continue
            try:
                tree = await asyncio.to_thread(self._content.read, entry.source.tree.digest)
            except ContentStoreError as exc:
                failures.append(ResolveFailure(name, f"the skill package's bytes are not in the store: {exc}"))
                continue
            files = decode_tree_bytes(tree)
            if files is None:
                failures.append(ResolveFailure(name, "the stored skill package is not an acm-tree-v1 tree"))
                continue
            try:
                # A large package re-packed is seconds of CPU and a dry run
                # runs this on the request event loop: off the loop.
                validated = await asyncio.to_thread(validate_package, self._validator.validate_directory, files)
            except PackageRefusal as exc:
                failures.append(ResolveFailure(name, str(exc)))
                continue
            if validated.name != name:
                failures.append(
                    ResolveFailure(
                        name,
                        f"the package names its skill {validated.name!r}, but the entry declares {name!r}: the "
                        "report names entries as declared, and the report and the runtime name must agree",
                    )
                )
                continue
            intents.append(Intent(name, _SkillPackage(validated.name, validated.canonical_zip, note=entry.note)))

        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """Classify against Direct names and include inactive Local cleanup."""
        supplied = self._set_supplied(ctx)
        area = _without(self._area(ctx), supplied)
        # A package a set references — an inactive set's too — is that set's to keep: never cleaned up here.
        referenced = self._set_referenced_ids(ctx)
        local_assets = {
            name: asset
            for name, asset in _without(self._local_assets(ctx), supplied).items()
            if int(asset.skill_id) not in referenced
        }
        declared = {intent.identity for intent in intents}

        planned = [
            PlannedEntry(
                intent,
                (
                    EntryOutcome.UPDATED.value
                    if intent.identity in area
                    else EntryOutcome.CREATED.value
                ),
                requires_write=True,
            )
            for intent in intents
        ]
        removals = tuple(sorted((set(area) | set(local_assets)) - declared))
        # Local package replacement deliberately preserves the row's scanned
        # ``mcp_dependencies``; package validation/upload neither parses nor
        # rewrites them. A new Local row therefore starts dependency-free.
        # The declared names already present in this catalog are consequently
        # the exact post-write dependency projection, including Dry-run.
        retained_local = [
            local_assets[name] for name in declared if name in local_assets
        ]
        ctx.capability_state.final_skill_dependency_codes = self._dependency_codes(
            retained_local
        )
        return CategoryPlan(entries=tuple(planned), removals=removals)

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Fully write declarations, establish Direct claims, then clean omissions."""
        results: list[EntryResult] = []
        confirmed_write = False
        try:
            for planned in plan.entries:
                package = planned.intent.value
                if isinstance(package, _SharedSkill):
                    # The shared asset itself is claimed; nothing is uploaded or copied.
                    skill_id, note = package.skill_id, None
                else:
                    uploaded = await self._uploads.upload_local_skill(
                        bot_id=ctx.bot_id,
                        owner_id=ctx.entity_id,
                        actor_id=ctx.actor_id,
                        package=package.canonical_zip,
                    )
                    # A returned upload means the complete Local package and row
                    # have committed, even if the following Direct claim fails.
                    confirmed_write = True
                    skill_id, note = str(uploaded["skill"]["id"]), package.note
                await self._activation.claim_apply_skill(
                    skill_id=skill_id,
                    bot_id=ctx.bot_id,
                    owner_id=ctx.entity_id,
                    actor_id=ctx.actor_id,
                    apply_id=ctx.apply_id,
                )
                # A returned claim is a committed write too, a shared asset's included.
                confirmed_write = True
                results.append(
                    EntryResult(
                        self.construct,
                        planned.intent.identity,
                        EntryOutcome(planned.outcome),
                        note=note,
                    )
                )

            # Re-read identities for removals; the plan deliberately carries
            # names rather than persistence ids.
            area = _without(self._assets_for_removal(ctx), self._set_supplied(ctx))
            referenced = self._set_referenced_ids(ctx)
            for name in plan.removals:
                asset = area.get(name)
                if asset is None:
                    continue
                is_local = str(asset.git_path or "").startswith("local://")
                await self._activation.remove_apply_skill(
                    skill_id=str(asset.skill_id),
                    bot_id=ctx.bot_id,
                    owner_id=ctx.entity_id,
                    actor_id=ctx.actor_id,
                    apply_id=ctx.apply_id,
                )
                confirmed_write = True
                # Its Direct installation is gone; a package a set still references is not deleted.
                if is_local and int(asset.skill_id) not in referenced:
                    try:
                        await self._uploads.delete_local_skill(
                            skill_id=str(asset.skill_id),
                            name=name,
                            bot_id=ctx.bot_id,
                            owner_id=ctx.entity_id,
                            actor_id=ctx.actor_id,
                        )
                    except Exception:
                        raise ConfirmedPartialWriteError(
                            "Local Skill asset cleanup failed"
                        ) from None
        except Exception as exc:
            self._refresh_actual_dependency_codes(ctx)
            if isinstance(exc, ConfirmedPartialWriteError):
                raise
            if confirmed_write or isinstance(
                exc, ApplyDesiredStateCommittedError
            ):
                raise ConfirmedPartialWriteError(
                    "Skill replacement stopped after a durable write"
                ) from None
            raise
        self._refresh_actual_dependency_codes(ctx)
        return tuple(results)

    def _area(self, ctx: ApplyContext) -> dict[str, Any]:
        """The active set by name — the reader's flush-then-read for this bot."""
        return {
            asset.name: asset
            for asset in self._reader.active_skill_assets(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, bot=ctx.bot
            )
        }

    def _local_assets(self, ctx: ApplyContext) -> dict[str, Any]:
        return {
            asset.name: asset
            for asset in self._reader.local_skill_assets(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, bot=ctx.bot
            )
        }

    def _set_supplied(self, ctx: ApplyContext) -> frozenset[str]:
        """The runtime names the bot's skill sets supply; outside the declaration, whatever it says."""
        names, _mcps = self._activation.set_supplied_capabilities(
            bot_id=ctx.bot_id, owner_id=ctx.entity_id, actor_id=ctx.actor_id
        )
        return frozenset(names)

    def _set_referenced_ids(self, ctx: ApplyContext) -> frozenset[int]:
        """The skill ids any of the bot's sets holds, an inactive set's included; none before the bot exists."""
        if ctx.bot is None:
            return frozenset()
        return frozenset(int(skill_id) for skill_id in self._reader.member_skill_ids(bot=ctx.bot))

    async def set_conflicts(self, ctx: ApplyContext, entries: Sequence[DesiredSkill]) -> tuple[ResolveFailure, ...]:
        supplied = self._set_supplied(ctx)
        return tuple(
            ResolveFailure(
                entry.name,
                "a skill of this name is supplied by one of the bot's skill sets; declare it there or remove it "
                "from the set",
            )
            for entry in entries
            if entry.name in supplied
        )

    def _assets_for_removal(self, ctx: ApplyContext) -> dict[str, Any]:
        return {**self._area(ctx), **self._local_assets(ctx)}

    def _refresh_actual_dependency_codes(self, ctx: ApplyContext) -> None:
        """Never let a failed write leave the Dry-run projection in context."""
        ctx.capability_state.final_skill_dependency_codes = None
        try:
            assets = self._reader.active_skill_assets(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, bot=ctx.bot
            )
        except Exception:
            # MCP planning will retry the authoritative read and abort safely
            # if the state still cannot be observed.
            return
        ctx.capability_state.final_skill_dependency_codes = self._dependency_codes(
            assets
        )

    @staticmethod
    def _dependency_codes(assets: Sequence[Any]) -> frozenset[str]:
        codes: set[str] = set()
        for asset in assets:
            codes.update(
                mcp_dependency_codes(getattr(asset, "mcp_dependencies", ()) or ())
            )
        return frozenset(codes)


__all__ = ["SkillsMaterialiser"]
