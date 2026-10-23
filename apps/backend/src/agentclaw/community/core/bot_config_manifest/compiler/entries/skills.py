"""``manifest.skills`` → ``DesiredSkill``: a validated local package, stored as one tree.

The Manifest grammar declares local packages only (a ``source`` or ``from``
that delivers a zip, a tar.gz or a git tree); Skill Center and
shared-repository skills are the genome compiler's. The fetched bytes are
validated through the same ``SkillPackageValidator`` the manual upload runs,
and the package's files are filed as one ``acm-tree-v1`` blob — the shape the
genome stores a local skill in, so the two compilers agree on the digest.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DesiredSkill,
    EntryFailure,
    LocalSkillPackage,
    StoredContent,
)
from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import EntryDelivery
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.bot_config_manifest.compiler.entries._fetch import EntryFetchError, fetch, file_tree
from agentclaw.community.core.bot_config_manifest.compiler.session import CompileSession
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    ARCHIVE_MEMBER_LIMIT,
    FETCH_ENTRY_LIMITS,
    FetchCategory,
)
from agentclaw.community.core.bot_config_manifest.fetch.unpack import UnpackError, unpack_archive
from agentclaw.community.core.content_store import ContentStoreProtocol, canonical_tree_bytes, decode_tree_bytes
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageInvalidError,
    SkillPackageTooLargeError,
    SkillPackageValidator,
    ValidatedSkillPackage,
)

__all__ = ["compile_skills"]

_CATEGORY = FetchCategory.SKILLS.value

#: URL path suffix → the archive kind ``unpack_archive`` takes. Matched against
#: the delivery's ``source_url``, which on the object road is the
#: ``oss://bucket/key`` form, so the key's own extension decides.
_KIND_BY_SUFFIX: tuple[tuple[str, str], ...] = ((".zip", "zip"), (".tar.gz", "tar.gz"), (".tgz", "tar.gz"))

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


async def compile_skills(
    session: CompileSession,
    entries: Sequence[Mapping[str, Any]],
    *,
    resolver: DeclaredSourceResolver,
    content: ContentStoreProtocol,
    validator: SkillPackageValidator,
) -> Declaration[DesiredSkill]:
    """Every refusal up front: name conflicts before any bytes are spent, then the fetch, then the package."""
    desired: list[DesiredSkill] = []
    failures: list[EntryFailure] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        name = entry.get("name") if isinstance(entry, Mapping) else None
        if not isinstance(name, str) or not name:
            failures.append(EntryFailure(f"[{index}]", "a skills entry must name a 'name'"))
            continue
        if name in seen:
            failures.append(EntryFailure(name, "declared more than once in this category"))
            continue
        seen.add(name)
        if isinstance(entry.get("content"), str):
            failures.append(
                EntryFailure(
                    name,
                    "a skills entry is a package (SKILL.md + the files it names) — inline 'content' cannot be one; "
                    "declare 'source'",
                )
            )
            continue
        if "from" not in entry and not isinstance(entry.get("source"), (str, Mapping)):
            failures.append(EntryFailure(name, "a skills entry must declare 'source' or 'from'"))
            continue
        try:
            delivery = await fetch(session, resolver, entry=entry, category=_CATEGORY, identity=name)
        except EntryFetchError as exc:
            failures.append(EntryFailure(name, exc.reason))
            continue
        try:
            # A 100-MiB archive walked and re-packed is seconds of CPU, and a
            # dry run runs this on the request event loop: off the loop.
            if delivery.is_tree():
                validated = await asyncio.to_thread(_tree_package, delivery, validator)
            else:
                validated = await asyncio.to_thread(_blob_package, entry, delivery, validator)
        except _PackageRefusal as exc:
            failures.append(EntryFailure(name, str(exc)))
            continue
        if validated.name != name:
            failures.append(
                EntryFailure(
                    name,
                    f"the package names its skill {validated.name!r}, but the entry declares {name!r}: the report "
                    "names entries as declared, and the report and the runtime name must agree",
                )
            )
            continue
        tree = canonical_tree_bytes(list(validated.files))
        try:
            digest = await file_tree(session, content, delivery, tree, category=_CATEGORY, identity=name)
        except EntryFetchError as exc:
            failures.append(EntryFailure(name, exc.reason))
            continue
        desired.append(DesiredSkill(name, LocalSkillPackage(StoredContent(digest)), note=delivery.note()))
    return Declaration(tuple(desired), failures=tuple(failures))


def _tree_package(delivery: EntryDelivery, validator: SkillPackageValidator) -> ValidatedSkillPackage:
    """A delivered tree (a git checkout, or a stored tree decoded) → a validated package."""
    try:
        files = delivery.members(unpack=None, strip_components=0)
    except EntryFetchError as exc:
        raise _PackageRefusal(str(exc)) from exc
    if isinstance(files, str):
        raise _PackageRefusal(files)
    return _validate(validator.validate_directory, files)


def _blob_package(
    entry: Mapping[str, Any], delivery: EntryDelivery, validator: SkillPackageValidator
) -> ValidatedSkillPackage:
    """One delivered object → a validated package, the manual-upload shape.

    A fetched zip with no ``subpath`` goes byte-for-byte through
    ``validate_zip`` — the same validator the manual upload service runs, so
    limits and layout stay one rule — while an archive with a subpath (or a
    tar.gz) is unpacked and the selected subtree goes through
    ``validate_directory``.
    """
    archive = delivery.single()
    stored_tree = decode_tree_bytes(archive)
    if stored_tree is not None:
        # The platform's own copy of a git-delivered package, served back by
        # ``keep_last``: the compiler filed it as one tree blob, so it is
        # already the selected subtree and needs no unpack.
        return _validate(validator.validate_directory, list(stored_tree))
    kind = _archive_kind(entry, delivery.source_url() or "", delivery.content_type())
    subpath = entry.get("subpath")
    if kind == "zip" and not subpath:
        return _validate(validator.validate_zip, archive)
    files = _extract_subtree(archive, kind, subpath if isinstance(subpath, str) else None)
    return _validate(validator.validate_directory, files)


def _extract_subtree(archive: bytes, kind: str, subpath: Optional[str]) -> list[tuple[str, bytes]]:
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
            raise _PackageRefusal(f"the fetched skill archive could not be unpacked: {exc}") from exc
        selected: list[tuple[str, bytes]] = []
        for member in tree.members:
            relative = _under_subpath(member, subpath)
            if relative is None:
                continue
            # Read back from disk: the tree on disk is what the unpack guard
            # verified, permissions flattened, traversal refused.
            selected.append((relative, (root / member).read_bytes()))
        if not selected:
            raise _PackageRefusal(
                f"the archive contains nothing under subpath {subpath!r}"
                if subpath
                else "the archive contains no files"
            )
        return selected


def _archive_kind(entry: Mapping[str, Any], source_url: str, content_type: Optional[str]) -> str:
    """Declared ``unpack`` wins; else the URL's suffix; else the served media type; else refused."""
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
        "cannot tell how to unpack this skill source: neither its URL suffix nor its content type says 'zip' or "
        "'tar.gz', and 'unpack' is not declared"
    )


def _validate(call, payload) -> ValidatedSkillPackage:
    """One validator call, refused as an entry failure rather than a mid-write surprise."""
    try:
        return call(payload)
    except SkillPackageInvalidError as exc:
        raise _PackageRefusal(f"the fetched skill is not a valid package ({exc.reason})") from exc
    except SkillPackageTooLargeError:
        raise _PackageRefusal("the fetched skill package is over the upload package limits") from None


def _under_subpath(member: str, subpath: Optional[str]) -> Optional[str]:
    """The member's path relative to ``subpath``, or ``None`` if outside it (boundary-matched on segments)."""
    if subpath is None:
        return member
    prefix = subpath.rstrip("/")
    if not prefix:
        return member
    if member == prefix:
        return None
    if member.startswith(prefix + "/"):
        return member[len(prefix) + 1 :]
    return None
