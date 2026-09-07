"""``skills`` → local upload + direct activation — the active skill set.

The area is the one work-items §3.2 names: the bot's **active** skill set —
``BotCapabilityStateReader.active_skill_assets`` after its flush, the same
read the public listing answers from. Declared and not active ⇒ uploaded and
activated. Active and no longer declared ⇒ deactivated. Active and unchanged
⇒ no call at all (convergence). A skill one of the bot's SkillSets supplies
is neither: the write refuses it — the same narrowing the ``mcp``
materialiser applies to platform-default codes, asked up front so a governed
skill never becomes a mid-category abort, and never a removal.

**Packages travel the manual-upload road.** A no-subpath zip entry's fetched
bytes are validated by the same ``SkillPackageValidator`` the router path
uses, then handed to ``upload_local_skill`` as the canonical zip: an
installed skill is indistinguishable from an uploaded one because it *is* an
uploaded one (§3.3). A tar.gz or subpath entry is unpacked by the guarded
unpacker, its selected subtree re-packed canonically by the same validator.
The package's own SKILL.md front matter names the skill; a declaration whose
``name`` disagrees is refused — report identities would otherwise lie about
what got installed, and the runtime name is unique per bot either way.

Convergence compares **installed content**, not receipts: ``plan`` asks the
upload service for the digest of the package actually published under the
skill's name and marks `unchanged` only when it equals this entry's package
digest. A receipt proves the platform *fetched* the content — a dry run
files receipts without installing anything, and an aborted apply leaves a
receipt behind its failed write — so a receipt can never license an
unchanged verdict; it only dedups the fetch side.

Known corner, recorded rather than hidden: a skill a Set *references* but
whose skill id the flush's ``member_skill_ids`` excludes (an excluded
default-set member — the R1 refusal still applies to it) can abort a write
mid-category. The same class exists for user Sets in the ``mcp`` wave; both
report honestly as ``partially_written``.
"""
from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
    EntryFetcher,
    FetchedEntry,
    GitEntrySource,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    EntryOutcome,
    EntryResult,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    CategoryPlan,
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

#: URL path suffix → archive kind. Ordered so the longer tar suffix is tried
#: before a hypothetical shorter one; ``.tgz`` is the autoroute of the same
#: kind.
_KIND_BY_SUFFIX: tuple[tuple[str, str], ...] = (
    (".zip", "zip"),
    (".tar.gz", "tar.gz"),
    (".tgz", "tar.gz"),
)

#: Fallback when the URL does not say: the media type the source served.
_KIND_BY_CONTENT_TYPE: tuple[tuple[str, str], ...] = (
    ("application/zip", "zip"),
    ("application/x-zip-compressed", "zip"),
    ("application/gzip", "tar.gz"),
    ("application/x-gzip", "tar.gz"),
    ("application/x-tar", "tar.gz"),
)


class _PackageRefusal(Exception):
    """The fetched bytes can never become a skill package — refuse the entry."""


class _SkillPackage:
    """One entry's validated, upload-ready package — the intent's value."""

    __slots__ = ("name", "canonical_zip", "content_digest", "from_store", "note")

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
        # sha256 of the canonical zip: THE identity of the content this
        # entry installs. ``plan`` compares it with the digest of the bytes
        # actually published under the skill's name — the only honest
        # unchanged verdict, because a receipt only proves the platform
        # *fetched* this content, never that it was installed.
        self.content_digest = "sha256:" + hashlib.sha256(canonical_zip).hexdigest()
        # Whether the platform's own copy (W11) answered for the fetch — a
        # fetch-side fact only (no network was touched); it plays no part in
        # the unchanged verdict.
        self.from_store = from_store


class SkillsMaterialiser(Materialiser):
    """Converges the bot's active skills toward the declared package set."""

    construct = ManifestCategory.SKILLS

    def __init__(
        self,
        upload_service: SkillPackageUploadPort,
        activation_service: ActivationPort,
        capability_reader: BotCapabilityStateReaderProtocol,
        validator: SkillPackageValidator,
        fetcher: EntryFetcher,
    ) -> None:
        self._uploads = upload_service
        self._activation = activation_service
        self._reader = capability_reader
        self._validator = validator
        self._fetcher = fetcher

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

        area = {
            asset.name: asset
            for asset in self._reader.active_skill_assets(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, bot=ctx.bot
            )
        }
        governed = self._reader.member_skill_ids(bot=ctx.bot)

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

            existing = area.get(name)
            if existing is not None:
                if existing.skill_id in governed:
                    failures.append(
                        ResolveFailure(
                            name,
                            f"skill {name!r} is supplied to this bot by a skill set "
                            "or the default set: it is managed there, not by a "
                            "manifest, and a manifest can neither declare it nor "
                            "remove it",
                        )
                    )
                    continue
                if not str(existing.git_path or "").startswith("local://"):
                    failures.append(
                        ResolveFailure(
                            name,
                            f"an active non-local skill is already called {name!r}: "
                            "runtime skill names are unique per bot, so a local "
                            "package cannot be installed under its name",
                        )
                    )
                    continue

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
                decl = await asyncio.to_thread(
                    self._fetcher.fetch_declared,
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
                if isinstance(decl, GitEntrySource):
                    package = await asyncio.to_thread(
                        self._git_package, ctx, decl, name
                    )
                else:
                    package = await asyncio.to_thread(
                        self._build_package,
                        entry=entry,
                        fetched=decl,
                        source_url=decl.source_url
                        or (
                            entry["source"]
                            if isinstance(entry.get("source"), str)
                            else ""
                        ),
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
        """Classify each intent against the active set; compute removals.

        ``unchanged`` requires the **installed package's digest** to equal
        this entry's package digest — asked of the upload service, which
        reads the bytes actually published under the skill's name. The
        receipt a fetch filed proves the platform *fetched* the content, not
        that it was installed: a dry run files receipts without installing
        anything (a pin that moved between applies would never install: the
        name is active with the OLD package, the receipt matches the NEW
        pin, and the report would have said SUCCEEDED), and an apply whose
        write stage aborted leaves its resolve-stage receipts behind it. DNA
        of the installed state, not memory of the fetch, is the only honest
        verdict — and `None` (nothing installed, or unreadable) is unknown,
        never equal: the entry is classed for a full write.
        """
        area = self._area(ctx)
        governed = self._reader.member_skill_ids(bot=ctx.bot)
        declared = {intent.identity for intent in intents}

        planned = []
        for intent in intents:
            package = intent.value
            if intent.identity in area:
                installed_digest = await self._uploads.installed_package_digest(
                    bot=ctx.bot,
                    bot_id=ctx.bot_id,
                    owner_id=ctx.owner_id,
                    name=intent.identity,
                )
                outcome = (
                    EntryOutcome.UNCHANGED.value
                    if installed_digest == package.content_digest
                    else EntryOutcome.UPDATED.value
                )
            else:
                outcome = EntryOutcome.CREATED.value
            planned.append(PlannedEntry(intent, outcome))

        # The removal side of the area: what the write would refuse is not
        # planned for removal — a Set-supplied skill is not the manifest's.
        removable = {
            name
            for name, asset in area.items()
            if asset.skill_id not in governed
        }
        removals = tuple(sorted(removable - declared))
        return CategoryPlan(entries=tuple(planned), removals=removals)

    async def write(
        self, ctx: "ApplyContext", plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Upload what is not unchanged, activate what is not active yet.

        An ``unchanged`` entry calls nothing at all — convergence observed as
        the absence of writes, not equal-looking output. ``created`` activates
        the id the upload just wrote; ``updated`` does not re-activate (the
        skill is already in the active set — that is what ``updated`` meant).
        """
        results: list[EntryResult] = []
        for planned in plan.entries:
            if planned.outcome == EntryOutcome.UNCHANGED.value:
                results.append(
                    EntryResult(
                        self.construct,
                        planned.intent.identity,
                        EntryOutcome.UNCHANGED,
                    )
                )
                continue
            package = planned.intent.value
            uploaded = await self._uploads.upload_local_skill(
                bot_id=ctx.bot_id,
                owner_id=ctx.owner_id,
                actor_id=ctx.actor_id,
                package=package.canonical_zip,
            )
            if planned.outcome == EntryOutcome.CREATED.value:
                # The authoritative id: the row this very upload just wrote.
                skill_id = str(uploaded["skill"]["id"])
                await self._activation.activate_skill(
                    skill_id=skill_id,
                    bot_id=ctx.bot_id,
                    owner_id=ctx.owner_id,
                    actor_id=ctx.actor_id,
                )
            results.append(
                EntryResult(
                    self.construct,
                    planned.intent.identity,
                    EntryOutcome(planned.outcome),
                    # A keep_last fallback is a fact the report must state.
                    note=package.note,
                )
            )

        # Removals re-ask the area rather than carrying ids through the plan:
        # the engine's plan carries identities, materialisers are stateless
        # across stages by design, and one extra read cannot disagree with
        # itself the way a cached id can.
        area = self._area(ctx)
        for name in plan.removals:
            asset = area.get(name)
            if asset is None:
                # Gone between plan and write — already converged for this
                # name; there is nothing to deactivate and no error to report.
                continue
            await self._activation.deactivate_skill(
                skill_id=str(asset.skill_id),
                bot_id=ctx.bot_id,
                owner_id=ctx.owner_id,
                actor_id=ctx.actor_id,
            )
        return tuple(results)

    # ── the package road ────────────────────────────────────────────────────

    def _build_package(
        self, *, entry: dict[str, Any], fetched: FetchedEntry, source_url: str
    ) -> _SkillPackage:
        """Fetched bytes → a validated package, the manual-upload shape."""
        kind = self._archive_kind(entry, source_url, fetched.content_type)
        subpath = entry.get("subpath")

        if kind == "zip" and not subpath:
            # The byte-for-byte manual road: the fetched zip is validated and
            # its canonical form handed on — the same ``validate_zip`` the
            # upload service itself runs, so limits and layout are one rule.
            validated = self._validate(self._validator.validate_zip, fetched.content)
            return _SkillPackage(
                validated.name,
                validated.canonical_zip,
                from_store=fetched.from_store,
                note=fetched.fallback_reason,
            )

        files = self._extract_subtree(
            fetched.content, kind, subpath if isinstance(subpath, str) else None
        )
        validated = self._validate(self._validator.validate_directory, files)
        return _SkillPackage(
            validated.name,
            validated.canonical_zip,
            from_store=fetched.from_store,
            note=fetched.fallback_reason,
        )

    def _git_package(
        self, ctx: "ApplyContext", decl: GitEntrySource, name: str
    ) -> _SkillPackage:
        """A git checkout's tree → a validated package, plus its W11 receipt.

        The canonical zip the validator returns is what this entry delivers,
        so it is also what the platform stores: the receipt a later keep_last
        falls back to must be the deliverable bytes, not a re-derivation.
        """
        try:
            files = decl.files()
        except EntryFetchError as exc:
            raise _PackageRefusal(str(exc)) from exc
        validated = self._validate(self._validator.validate_directory, files)
        try:
            self._fetcher.file_bytes(
                ctx,
                content=validated.canonical_zip,
                source_url=decl.receipt_url(),
                category=_FETCH_CATEGORY,
                entry_identity=name,
                content_type="application/zip",
                credential_name=decl.auth,
            )
        except EntryFetchError as exc:
            raise _PackageRefusal(str(exc)) from exc
        return _SkillPackage(
            validated.name,
            validated.canonical_zip,
            from_store=False,
            note=decl.moved_note(),
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
