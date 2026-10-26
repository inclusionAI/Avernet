"""Materialise the desired skills as the bot's complete Skill and Bot-owned Local snapshot.

Every declared local package is validated and fully written through the normal
Local package road, then converted to a Direct claim. Ordinary SkillSet
memberships with the same runtime name are detached; Default supply is
excluded. Existing effective names report ``updated`` and inactive or absent
names report ``created``—package equality is deliberately not probed.

A declared Skill Center or shared-repository skill is **skipped** for now
(inclusionAI/Avernet#3103 tracks its delivery): it is logged at warning,
written nowhere, counted as declared so nothing of that name is removed, and
reported ``skipped`` with the reason, so the apply's summary still says that
not everything declared was delivered. Rewriting it as a local package would
copy a governed asset into bot-owned storage, so that road is not taken.

When the section is present, every omitted effective Skill is removed and every
omitted Bot-owned Local asset is additionally unreferenced, physically deleted,
and removed from the catalog. Shared Repo/Center assets are never physically
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
from agentclaw.community.core.apply_engine.desired_state import DesiredSkill, LocalSkillPackage, SkillOrigin
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
    Materialiser,
    PlannedEntry,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.apply_engine.capabilities import (
    Category,
)
from agentclaw.community.core.ports.activation_port import ActivationPort
from agentclaw.community.core.ports.skill_package_upload_port import (
    SkillPackageUploadPort,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    ManifestDesiredStateCommittedError,
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
class _NotDelivered:
    """The intent of a skill this build does not deliver: carried through the plan so its name counts as declared."""

    origin: SkillOrigin

    @property
    def reason(self) -> str:
        return (
            f"not delivered: a {self.origin.value} skill is skipped by this build (inclusionAI/Avernet#3103); "
            "nothing was written or removed"
        )


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


class SkillsMaterialiser(Materialiser):
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
    ) -> None:
        self._uploads = upload_service
        self._activation = activation_service
        self._reader = capability_reader
        self._validator = validator
        self._content = content

    async def resolve(self, ctx: ApplyContext, entries: Sequence[DesiredSkill]) -> ResolveResult:
        """Desired entries → validated packages, every refusal up front.

        A local package's stored tree goes through ``validate_directory`` —
        the same validator the manual upload service runs, so limits and
        layout stay one rule — and its canonical zip is what the upload takes.
        A Skill Center or shared-repository skill is skipped (see the module
        docstring): a warning, a placeholder intent, and the rest of the
        category proceeds.
        """
        intents: list[Intent] = []
        failures: list[ResolveFailure] = []
        for entry in entries:
            name = entry.name
            if not isinstance(entry.source, LocalSkillPackage):
                logger.warning(
                    "[apply] skill %r is a %s skill, which this build does not deliver; skipped for bot %s "
                    "(nothing written or removed; inclusionAI/Avernet#3103)",
                    name,
                    entry.origin.value,
                    ctx.bot_id,
                )
                intents.append(Intent(name, _NotDelivered(entry.origin)))
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
        """Classify against effective names and include inactive Local cleanup."""
        area = self._area(ctx)
        local_assets = self._local_assets(ctx)
        declared = {intent.identity for intent in intents}

        planned = [
            (
                PlannedEntry(intent, EntryOutcome.SKIPPED.value)
                if isinstance(intent.value, _NotDelivered)
                else PlannedEntry(
                    intent,
                    (
                        EntryOutcome.UPDATED.value
                        if intent.identity in area
                        else EntryOutcome.CREATED.value
                    ),
                    requires_write=True,
                )
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
                if isinstance(package, _NotDelivered):
                    results.append(
                        EntryResult(
                            self.construct, planned.intent.identity, EntryOutcome.SKIPPED, reason=package.reason
                        )
                    )
                    continue
                uploaded = await self._uploads.upload_local_skill(
                    bot_id=ctx.bot_id,
                    owner_id=ctx.entity_id,
                    actor_id=ctx.actor_id,
                    package=package.canonical_zip,
                )
                # A returned upload means the complete Local package and row
                # have committed, even if the following Direct claim fails.
                confirmed_write = True
                skill_id = str(uploaded["skill"]["id"])
                await self._activation.claim_manifest_skill(
                    skill_id=skill_id,
                    bot_id=ctx.bot_id,
                    owner_id=ctx.entity_id,
                    actor_id=ctx.actor_id,
                    apply_id=ctx.apply_id,
                )
                results.append(
                    EntryResult(
                        self.construct,
                        planned.intent.identity,
                        EntryOutcome(planned.outcome),
                        note=package.note,
                    )
                )

            # Re-read identities for removals; the plan deliberately carries
            # names rather than persistence ids.
            area = self._assets_for_removal(ctx)
            for name in plan.removals:
                asset = area.get(name)
                if asset is None:
                    continue
                is_local = str(asset.git_path or "").startswith("local://")
                await self._activation.remove_manifest_skill(
                    skill_id=str(asset.skill_id),
                    bot_id=ctx.bot_id,
                    owner_id=ctx.entity_id,
                    actor_id=ctx.actor_id,
                    apply_id=ctx.apply_id,
                    remove_inactive_memberships=is_local,
                )
                confirmed_write = True
                if is_local:
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
                exc, ManifestDesiredStateCommittedError
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
