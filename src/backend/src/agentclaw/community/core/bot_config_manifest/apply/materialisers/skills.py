"""Materialise the Manifest's complete Skill and Bot-owned Local snapshot.

Every declared package is validated and fully written through the normal Local
package road, then converted to a Direct claim. Ordinary SkillSet memberships
with the same runtime name are detached; Default supply is excluded. Existing
effective names report ``updated`` and inactive or absent names report
``created``—package equality is deliberately not probed.

When the section is present, every omitted effective Skill is removed and every
omitted Bot-owned Local asset is additionally unreferenced, physically deleted,
and removed from the catalog. Shared Repo/Center assets are never physically
deleted. The complete Local catalog, not only active Installations, makes failed
cleanup discoverable on a later Apply.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    EntryDelivery,
    EntryFetchError,
)
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import (
    DeclaredSourceResolver,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    EntryOutcome,
    EntryResult,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    CategoryPlan,
    ConfirmedPartialWriteError,
    Intent,
    Materialiser,
    PlannedEntry,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FetchCategory,
    ARCHIVE_MEMBER_LIMIT,
    FETCH_ENTRY_LIMITS,
)
from agentclaw.community.core.bot_config_manifest.fetch.unpack import (
    UnpackError,
    unpack_archive,
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
    SkillPackageInvalidError,
    SkillPackageTooLargeError,
    SkillPackageValidator,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.apply.context import (
        ApplyContext,
    )

logger = get_logger()

_FETCH_CATEGORY = FetchCategory.SKILLS

#: URL path suffix → the archive kind ``unpack_archive`` takes::
#:
#:     "https://content.example/skills/quality-check.zip"  -> "zip"
#:     "https://content.example/skills/qc.tar.gz"          -> "tar.gz"
#:     "https://content.example/skills/qc.tgz"             -> "tar.gz"
#:
#: Matched against the delivery's ``source_url``, which on the object road is
#: the ``oss://bucket/key`` form — so the key's own extension is what decides.
#: Tried before :data:`_KIND_BY_CONTENT_TYPE`.
_KIND_BY_SUFFIX: tuple[tuple[str, str], ...] = (
    (".zip", "zip"),
    (".tar.gz", "tar.gz"),
    (".tgz", "tar.gz"),
)

#: Fallback when the URL does not say: the media type the source served, as
#: ``EntryDelivery.content_type()`` reports it. ``None`` there — which is
#: always the case on the git road and usual on the object road — means the
#: source said nothing, and the entry is refused rather than guessed at.
_KIND_BY_CONTENT_TYPE: tuple[tuple[str, str], ...] = (
    ("application/zip", "zip"),
    ("application/x-zip-compressed", "zip"),
    ("application/gzip", "tar.gz"),
    ("application/x-gzip", "tar.gz"),
    ("application/x-tar", "tar.gz"),
)


class _PackageRefusal(Exception):
    """The fetched bytes can never become a skill package — refuse the entry.

    Internal to this module: raised by the packaging helpers and caught in
    ``resolve``, which turns ``str(exc)`` into the entry's
    :class:`~...registry.ResolveFailure` reason. Distinct from
    :class:`~...entry_delivery.EntryFetchError`, which means the bytes never
    arrived at all.
    """


class _SkillPackage:
    """One entry's validated, upload-ready package — the intent's value.

    Carried as ``Intent.value``, so a planned entry reads
    ``planned.intent.value.canonical_zip``::

        _SkillPackage(
            name="quality-check",          # from the package's SKILL.md
            canonical_zip=b"PK\x03\x04...",  # what upload_local_skill takes
            from_store=False,
            note=None,
        )

    On a ``keep_last`` fallback the same object carries the reason, which the
    write puts on the entry's report row::

        _SkillPackage(
            name="quality-check",
            canonical_zip=b"PK\x03\x04...",
            from_store=True,
            note=("delivered from the platform's stored copy (keep_last): "
                  "the source fetch failed — ..."),
        )

    Created by: :meth:`SkillsMaterialiser.resolve`.
    Consumed by: that materialiser's ``plan`` and ``write``.
    """

    __slots__ = ("name", "canonical_zip", "from_store", "note")

    def __init__(
        self,
        name: str,
        canonical_zip: bytes,
        *,
        from_store: bool,
        note: "str | None" = None,
    ) -> None:
        self.name = name
        self.canonical_zip = canonical_zip
        # A keep_last fallback's reason, surfaced on the report row — see
        # Intent.note for why it travels inside the value.
        self.note = note
        # Whether the platform's own copy (W11) answered for the fetch — a
        # fetch-side fact only (no network was touched); it plays no part in
        # the unchanged verdict.
        self.from_store = from_store


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
        write   -> (EntryResult(ManifestCategory.SKILLS, "quality-check",
                                EntryOutcome.CREATED),)
    """

    construct = ManifestCategory.SKILLS

    def __init__(
        self,
        upload_service: SkillPackageUploadPort,
        activation_service: ActivationPort,
        capability_reader: BotCapabilityStateReaderProtocol,
        validator: SkillPackageValidator,
        resolver: DeclaredSourceResolver,
    ) -> None:
        self._uploads = upload_service
        self._activation = activation_service
        self._reader = capability_reader
        self._validator = validator
        self._resolver = resolver

    async def resolve(
        self, ctx: "ApplyContext", entries: Sequence[dict[str, Any]]
    ) -> ResolveResult:
        """Declared entries → validated packages, every refusal up front.

        The order is deliberate: the name-level conflicts a declaration can
        already have with the area are asked **before** any bytes are spent,
        because the fetch is the expensive failure; then the fetch/pin; then
        the package's own shape and name.
        """
        intents: list[Intent] = []
        failures: list[ResolveFailure] = []
        seen: set[str] = set()

        for index, entry in enumerate(entries):
            name = entry.get("name") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not name:
                failures.append(
                    ResolveFailure(f"[{index}]", "a skills entry must name a 'name'")
                )
                continue
            if name in seen:
                # The active set is keyed by name — a duplicate declaration
                # states something untrue of any result.
                failures.append(
                    ResolveFailure(name, "declared more than once in this category")
                )
                continue
            seen.add(name)

            inline = entry.get("content")
            if isinstance(inline, str):
                # The belt behind the validator's PUT-time rule: a skill is a
                # package, and no materialiser exists for inline text.
                failures.append(
                    ResolveFailure(
                        name,
                        "a skills entry is a package (SKILL.md + the files it "
                        "names) — inline 'content' cannot be one; declare 'source'",
                    )
                )
                continue

            if "from" not in entry and not isinstance(
                entry.get("source"), (str, dict)
            ):
                failures.append(
                    ResolveFailure(
                        name, "a skills entry must declare 'source' or 'from'"
                    )
                )
                continue

            try:
                # Blocking network + disk I/O (W2's sync transport, W11's
                # blob write) off the event loop — see the identity
                # materialiser's note; a dry run must not park the server on
                # a hung source.
                delivery = await asyncio.to_thread(
                    self._resolver.resolve,
                    ctx,
                    entry=entry,
                    category=_FETCH_CATEGORY,
                    entry_identity=name,
                )
            except EntryFetchError as exc:
                failures.append(ResolveFailure(name, exc.reason))
                continue

            try:
                # The same blocking-IO ruling as the fetch itself: a
                # 100-MiB archive walked and re-packed is seconds of CPU and
                # a dry run runs this on the request event loop — the fetch
                # got to_thread for exactly that reason (and the module's
                # own comment says it).
                # The one branch this category keeps, and it asks what
                # *arrived* rather than which class produced it. The two roads
                # run two validators on purpose: a fetched zip with no
                # ``subpath`` goes byte-for-byte through ``validate_zip`` — the
                # same validator the manual upload service runs, so limits and
                # layout stay one rule — while a tree goes through
                # ``validate_directory``. Collapsing them would discard the
                # byte-for-byte road.
                if delivery.is_tree():
                    package = await asyncio.to_thread(
                        self._tree_package, ctx, delivery, name
                    )
                else:
                    package = await asyncio.to_thread(
                        self._build_package,
                        entry=entry,
                        delivery=delivery,
                        source_url=delivery.source_url() or "",
                    )
            except _PackageRefusal as exc:
                failures.append(ResolveFailure(name, str(exc)))
                continue

            if package.name != name:
                failures.append(
                    ResolveFailure(
                        name,
                        f"the package names its skill {package.name!r}, but the "
                        f"entry declares {name!r}: the report names entries as "
                        "declared, and the report and the runtime name must agree",
                    )
                )
                continue

            intents.append(Intent(name, package))

        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

    async def plan(
        self, ctx: "ApplyContext", intents: Sequence[Intent]
    ) -> CategoryPlan:
        """Classify against effective names and include inactive Local cleanup."""
        area = self._area(ctx)
        local_assets = self._local_assets(ctx)
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
        retained_local = [
            local_assets[name] for name in declared if name in local_assets
        ]
        ctx.capability_state.final_skill_dependency_codes = self._dependency_codes(
            retained_local
        )
        return CategoryPlan(entries=tuple(planned), removals=removals)

    async def write(
        self, ctx: "ApplyContext", plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Fully write declarations, establish Direct claims, then clean omissions."""
        results: list[EntryResult] = []
        confirmed_write = False
        try:
            for planned in plan.entries:
                package = planned.intent.value
                uploaded = await self._uploads.upload_local_skill(
                    bot_id=ctx.bot_id,
                    owner_id=ctx.owner_id,
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
                    owner_id=ctx.owner_id,
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
                    owner_id=ctx.owner_id,
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
                            owner_id=ctx.owner_id,
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

    # ── the package road ────────────────────────────────────────────────────

    def _build_package(
        self, *, entry: dict[str, Any], delivery: EntryDelivery, source_url: str
    ) -> _SkillPackage:
        """One delivered object → a validated package, the manual-upload shape."""
        kind = self._archive_kind(entry, source_url, delivery.content_type())
        subpath = entry.get("subpath")
        content = delivery.single()

        if kind == "zip" and not subpath:
            # The byte-for-byte manual road: the fetched zip is validated and
            # its canonical form handed on — the same ``validate_zip`` the
            # upload service itself runs, so limits and layout are one rule.
            validated = self._validate(self._validator.validate_zip, content)
            return _SkillPackage(
                validated.name,
                validated.canonical_zip,
                from_store=delivery.from_store(),
                note=delivery.note(),
            )

        files = self._extract_subtree(
            content, kind, subpath if isinstance(subpath, str) else None
        )
        validated = self._validate(self._validator.validate_directory, files)
        return _SkillPackage(
            validated.name,
            validated.canonical_zip,
            from_store=delivery.from_store(),
            note=delivery.note(),
        )

    def _tree_package(
        self, ctx: "ApplyContext", delivery: EntryDelivery, name: str
    ) -> _SkillPackage:
        """A delivered tree → a validated package, plus its W11 receipt.

        The canonical zip the validator returns is what this entry delivers,
        so it is also what the platform stores: the receipt a later keep_last
        falls back to must be the deliverable bytes, not a re-derivation.
        That is exactly why the filing happens **here** and not behind the
        delivery seam — the bytes worth a receipt are the ones validation
        produced, which only this category knows how to make.
        """
        try:
            files = delivery.members(unpack=None, strip_components=0)
        except EntryFetchError as exc:
            raise _PackageRefusal(str(exc)) from exc
        if isinstance(files, str):
            raise _PackageRefusal(files)
        validated = self._validate(self._validator.validate_directory, files)
        try:
            delivery.file(
                ctx,
                validated.canonical_zip,
                category=_FETCH_CATEGORY,
                entry_identity=name,
                content_type="application/zip",
            )
        except EntryFetchError as exc:
            raise _PackageRefusal(str(exc)) from exc
        return _SkillPackage(
            validated.name,
            validated.canonical_zip,
            from_store=delivery.from_store(),
            note=delivery.note(),
        )

    def _extract_subtree(
        self, archive: bytes, kind: str, subpath: str | None
    ) -> list[tuple[str, bytes]]:
        """The guarded unpack, then the subpath's files as (relpath, bytes)."""
        with tempfile.TemporaryDirectory(prefix="manifest-skill-") as tmp:
            root = Path(tmp) / "pkg"
            try:
                tree = unpack_archive(
                    archive,
                    kind,
                    root,
                    member_limit=ARCHIVE_MEMBER_LIMIT,
                    unpacked_size_limit=FETCH_ENTRY_LIMITS["resources_unpacked"],
                )
            except UnpackError as exc:
                raise _PackageRefusal(
                    f"the fetched skill archive could not be unpacked: {exc}"
                ) from exc

            selected: list[tuple[str, bytes]] = []
            for member in tree.members:
                relative = _under_subpath(member, subpath)
                if relative is None:
                    continue
                # Read back from disk: the tree on disk is what the unpack
                # guard verified, permissions flattened, traversal refused.
                selected.append((relative, (root / member).read_bytes()))
            if not selected:
                raise _PackageRefusal(
                    f"the archive contains nothing under subpath {subpath!r}"
                    if subpath
                    else "the archive contains no files"
                )
            return selected

    def _archive_kind(self, entry: dict[str, Any], source_url: str, content_type: str | None) -> str:
        """Declared ``unpack`` wins; else the URL's suffix; else the served
        media type; else the entry is refused — fetching a skill whose
        delivery shape nobody can name would fail inside the write."""
        declared = entry.get("unpack")
        if declared in ("zip", "tar.gz"):
            return declared
        path = source_url.split("?", 1)[0].lower()
        for suffix, kind in _KIND_BY_SUFFIX:
            if path.endswith(suffix):
                return kind
        if isinstance(content_type, str):
            media = content_type.split(";", 1)[0].strip().lower()
            for ctype, kind in _KIND_BY_CONTENT_TYPE:
                if media == ctype:
                    return kind
        raise _PackageRefusal(
            "cannot tell how to unpack this skill source: neither its URL "
            "suffix nor its content type says 'zip' or 'tar.gz', and 'unpack' "
            "is not declared"
        )

    def _validate(self, call, payload):
        """One validator call, refused as an entry failure — at resolve time,
        in the all-or-nothing envelope, never as a mid-write surprise."""
        try:
            return call(payload)
        except SkillPackageInvalidError as exc:
            raise _PackageRefusal(
                f"the fetched skill is not a valid package ({exc.reason})"
            ) from exc
        except SkillPackageTooLargeError:
            raise _PackageRefusal(
                "the fetched skill package is over the upload package limits"
            ) from None

    def _area(self, ctx: "ApplyContext") -> dict[str, Any]:
        """The active set by name — the reader's flush-then-read for this bot."""
        return {
            asset.name: asset
            for asset in self._reader.active_skill_assets(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, bot=ctx.bot
            )
        }

    def _local_assets(self, ctx: "ApplyContext") -> dict[str, Any]:
        return {
            asset.name: asset
            for asset in self._reader.local_skill_assets(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, bot=ctx.bot
            )
        }

    def _assets_for_removal(self, ctx: "ApplyContext") -> dict[str, Any]:
        return {**self._area(ctx), **self._local_assets(ctx)}

    def _refresh_actual_dependency_codes(self, ctx: "ApplyContext") -> None:
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


def _under_subpath(member: str, subpath: str | None) -> str | None:
    """The member's path relative to ``subpath``, or ``None`` if outside it.

    Boundary-matched on segments: ``pkg/skill-a`` is not under
    ``pkg/skill``. Both are already workspace-relative with no ``..``
    segments — the entry's ``subpath`` by the validator, the member's name by
    the unpack guard — so the comparison is pure prefix arithmetic.
    """
    if subpath is None:
        return member
    prefix = subpath.rstrip("/")
    if not prefix:
        return member
    if member == prefix:
        # The subpath itself: a directory member, never a file.
        return None
    if member.startswith(prefix + "/"):
        return member[len(prefix) + 1 :]
    return None


__all__ = ["SkillsMaterialiser"]
