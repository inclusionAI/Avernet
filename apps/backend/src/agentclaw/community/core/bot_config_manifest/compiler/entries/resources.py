"""``manifest.resources`` → ``DesiredResourceFile`` / ``DesiredResourceTree``.

A file entry is one stored object (inline ``content`` filed as produced
content, or the fetched single file). A directory entry (``path`` ending in
``/``) is fetched, unpacked on the platform side, and filed as one
``acm-tree-v1`` blob. The grammar rules a stored document can predate are
re-asked here: the path rule, the resources root (``data/``), duplicates,
and the nesting ban. Admission (extension allow-list, size cap) is the
engine's, asked by the materialiser against the stored bytes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from agentclaw.community.core.apply_engine.desired_state import (
    RESOURCES_ROOT,
    Declaration,
    DesiredResource,
    DesiredResourceFile,
    DesiredResourceTree,
    EntryFailure,
    StoredContent,
)
from agentclaw.community.core.apply_engine.paths import relative_path_refusal
from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import archive_refusal
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import (
    DeclaredSourceResolver,
    declared_protocol,
)
from agentclaw.community.core.bot_config_manifest.compiler.entries._fetch import (
    ORIGIN_INLINE,
    EntryFetchError,
    fetch,
    file_produced,
    file_single,
    file_tree,
)
from agentclaw.community.core.bot_config_manifest.compiler.session import CompileSession, digest_of
from agentclaw.community.core.bot_config_manifest.fetch.limits import FetchCategory
from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind
from agentclaw.community.core.content_store import ContentStoreProtocol, canonical_tree_bytes

__all__ = ["OUTSIDE_ROOT_REASON", "compile_resources"]

_CATEGORY_FILE = FetchCategory.RESOURCES_FILE.value
_CATEGORY_ARCHIVE = FetchCategory.RESOURCES_ARCHIVE.value

#: The reason a resource path outside the root is refused with; the schema says the same at ``PUT``.
OUTSIDE_ROOT_REASON = f"resource path must be under the resources root {RESOURCES_ROOT!r}"


async def compile_resources(
    session: CompileSession,
    entries: Sequence[Mapping[str, Any]],
    *,
    resolver: DeclaredSourceResolver,
    content: ContentStoreProtocol,
) -> Declaration[DesiredResource]:
    failures: list[EntryFailure] = []
    usable: list[tuple[str, Mapping[str, Any]]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        path = entry.get("path") if isinstance(entry, Mapping) else None
        if not isinstance(path, str) or not path:
            failures.append(EntryFailure(f"[{index}]", "a resources entry must declare a 'path'"))
            continue
        refusal = relative_path_refusal(path, what="path")
        if refusal is not None:
            failures.append(EntryFailure(path, refusal[1]))
            continue
        if path in seen:
            failures.append(EntryFailure(path, "a resources path is declared more than once in this category"))
            continue
        seen.add(path)
        if not path.startswith(RESOURCES_ROOT) or path == RESOURCES_ROOT:
            failures.append(EntryFailure(path, OUTSIDE_ROOT_REASON))
            continue
        usable.append((path, entry))

    # The nesting ban: a directory entry owns its whole subtree, so an entry
    # under another declared directory has two owners with no precedence.
    directories = [path for path, _ in usable if path.endswith("/")]
    nested: dict[str, str] = {}
    for path, _ in usable:
        for directory in directories:
            if path != directory and (path.startswith(directory) or path + "/" == directory):
                nested.setdefault(path, directory)
    for path, directory in nested.items():
        failures.append(EntryFailure(path, f"path nests under another declared directory {directory!r}"))

    desired: list[DesiredResource] = []
    for path, entry in usable:
        if path in nested:
            continue
        try:
            if path.endswith("/"):
                desired.append(await _directory(session, entry, path, resolver=resolver, content=content))
            else:
                desired.append(await _file(session, entry, path, resolver=resolver, content=content))
        except EntryFetchError as exc:
            failures.append(EntryFailure(path, exc.reason))
    return Declaration(tuple(desired), failures=tuple(failures))


async def _file(
    session: CompileSession,
    entry: Mapping[str, Any],
    path: str,
    *,
    resolver: DeclaredSourceResolver,
    content: ContentStoreProtocol,
) -> DesiredResourceFile:
    inline = entry.get("content")
    if isinstance(inline, str):
        digest = await file_produced(
            session, content, inline.encode("utf-8"), origin=ORIGIN_INLINE, category=_CATEGORY_FILE, identity=path
        )
        return DesiredResourceFile(path, StoredContent(digest))
    delivery = await fetch(session, resolver, entry=entry, category=_CATEGORY_FILE, identity=path)
    data = await file_single(session, delivery, category=_CATEGORY_FILE, identity=path)
    return DesiredResourceFile(path, StoredContent(digest_of(data)), note=delivery.note())


async def _directory(
    session: CompileSession,
    entry: Mapping[str, Any],
    path: str,
    *,
    resolver: DeclaredSourceResolver,
    content: ContentStoreProtocol,
) -> DesiredResourceTree:
    # The archive fields are checked BEFORE the network, on the one road where
    # they apply: a fetch here can be 200 MiB against this compile's budget,
    # spent on an entry a missing 'unpack' guarantees to reject.
    if declared_protocol(session, entry) is SourceKind.OSS:
        refusal = archive_refusal(entry.get("unpack"), entry.get("strip_components", 0))
        if refusal is not None:
            raise EntryFetchError(refusal)
    delivery = await fetch(session, resolver, entry=entry, category=_CATEGORY_ARCHIVE, identity=path)
    # One call on every road: a git checkout answers with its tree, an archive
    # with its unpacked members, a keep_last fallback with the stored tree.
    members = await asyncio.to_thread(
        delivery.members, unpack=entry.get("unpack"), strip_components=entry.get("strip_components", 0)
    )
    if isinstance(members, str):
        raise EntryFetchError(members)
    tree = canonical_tree_bytes(members)
    digest = await file_tree(session, content, delivery, tree, category=_CATEGORY_ARCHIVE, identity=path)
    return DesiredResourceTree(path, StoredContent(digest), note=delivery.note())
