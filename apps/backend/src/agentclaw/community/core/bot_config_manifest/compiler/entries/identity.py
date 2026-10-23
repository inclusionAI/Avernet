"""``manifest.identity`` → ``DesiredIdentityFile``: one stored file per declared type.

Inline ``content`` is filed as produced content; a fetched entry goes
through the funnel and the delivery's one file is filed under its receipt.
Which types the engine accepts, and the reserved names, are the
materialiser's to refuse: they are facts about the bot, not the document.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DesiredIdentityFile,
    EntryFailure,
    StoredContent,
)
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.bot_config_manifest.compiler.entries._fetch import (
    ORIGIN_INLINE,
    EntryFetchError,
    fetch,
    file_produced,
    file_single,
)
from agentclaw.community.core.bot_config_manifest.compiler.session import CompileSession, digest_of
from agentclaw.community.core.bot_config_manifest.fetch.limits import FetchCategory
from agentclaw.community.core.content_store import ContentStoreProtocol

__all__ = ["compile_identity"]

#: The identity cap is the smallest of the six, 1 MiB, because these are text files.
_CATEGORY = FetchCategory.IDENTITY.value


async def compile_identity(
    session: CompileSession,
    entries: Sequence[Mapping[str, Any]],
    *,
    resolver: DeclaredSourceResolver,
    content: ContentStoreProtocol,
) -> Declaration[DesiredIdentityFile]:
    desired: list[DesiredIdentityFile] = []
    failures: list[EntryFailure] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        file_type = entry.get("type") if isinstance(entry, Mapping) else None
        if not isinstance(file_type, str) or not file_type:
            failures.append(EntryFailure(f"[{index}]", "an identity entry must name a 'type'"))
            continue
        if file_type in seen:
            # The file set is keyed by type: a second declaration of one file
            # has no order with the first, so the document states something
            # untrue of any result.
            failures.append(EntryFailure(file_type, "declared more than once in this category"))
            continue
        seen.add(file_type)

        inline = entry.get("content")
        if isinstance(inline, str):
            digest = await file_produced(
                session, content, inline.encode("utf-8"), origin=ORIGIN_INLINE, category=_CATEGORY, identity=file_type
            )
            desired.append(DesiredIdentityFile(file_type, StoredContent(digest)))
            continue
        if "from" not in entry and "source" not in entry:
            failures.append(EntryFailure(file_type, "an identity entry must declare 'source', 'from' or 'content'"))
            continue
        try:
            delivery = await fetch(session, resolver, entry=entry, category=_CATEGORY, identity=file_type)
            body = await file_single(session, delivery, category=_CATEGORY, identity=file_type)
        except EntryFetchError as exc:
            # Includes the "must name a single file" refusal a tree delivery
            # raises when nothing selected one file out of it.
            failures.append(EntryFailure(file_type, exc.reason))
            continue
        desired.append(DesiredIdentityFile(file_type, StoredContent(digest_of(body)), note=delivery.note()))
    return Declaration(tuple(desired), failures=tuple(failures))
