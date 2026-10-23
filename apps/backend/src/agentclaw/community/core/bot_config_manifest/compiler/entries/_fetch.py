"""What every fetching entry compiler shares: the one funnel, and filing bytes so a digest is reachable.

Three roads put bytes in the content store on the way to a digest:

- a **fetched single object** (an identity file, a resource file, a CLI
  tool): the fetch filed it (object road) or ``delivery.file`` files it (git
  road), and the digest is what the delivery hands back;
- **inline** ``content:`` (identity, resources): never fetched, so filed as
  produced content under ``produced://manifest/inline``;
- a **tree the compiler built** (a skill package, a directory resource):
  ``delivery.file`` writes the receipt a later ``keep_last`` reads (a no-op
  on the object road, which filed the archive it fetched), and then the tree
  is filed as produced content unless a row for this bot already names it,
  so the digest the state carries is reachable whichever road it came by.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Optional

from agentclaw.community.core.bot_config_manifest.sources.entry_delivery import EntryDelivery, EntryFetchError
from agentclaw.community.core.bot_config_manifest.sources.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.bot_config_manifest.compiler.session import CompileSession, digest_of
from agentclaw.community.core.content_store import ContentStoreProtocol
from agentclaw.community.core.content_store.errors import ContentStoreError

__all__ = ["EntryFetchError", "fetch", "file_produced", "file_single", "file_tree"]

#: The produced-content origins the compiler files under.
ORIGIN_INLINE = "manifest/inline"
ORIGIN_TREE = "manifest/tree"
#: A fetched single file whose delivery filed no receipt the bot can reach (the belt, never the road).
ORIGIN_RECEIPT = "manifest/receipt"


async def fetch(
    session: CompileSession,
    resolver: DeclaredSourceResolver,
    *,
    entry: Mapping[str, Any],
    category: str,
    identity: str,
) -> EntryDelivery:
    """One entry's source through the W2/W3/W11 funnel, off the event loop.

    The fetch is blocking network I/O (W2's sync transport) and runs in a
    thread: a dry run awaits this inline, and a hung source must not park
    every concurrent request. Raises ``EntryFetchError`` with the reason the
    report shows.
    """
    return await asyncio.to_thread(resolver.resolve, session, entry=entry, category=category, entry_identity=identity)


async def file_single(
    session: CompileSession,
    content: ContentStoreProtocol,
    delivery: EntryDelivery,
    *,
    category: str,
    identity: str,
) -> bytes:
    """The one file the delivery names, filed; returns its bytes (the digest is ``digest_of`` them).

    The delivery files its own receipt (the object road did so inside the
    fetch; the git road writes it here). The reachability check after it is
    the belt: the state carries this digest, so the bot must hold a row for
    it, and a delivery that filed nowhere gets a produced row rather than a
    digest the materialiser cannot read.
    """

    def _read_and_file() -> bytes:
        body = delivery.single()
        delivery.file(session, body, category=category, entry_identity=identity)
        if not content.is_reachable(session.scope, digest_of(body)):
            _store_produced(
                content,
                body,
                scope=session.scope,
                origin=ORIGIN_RECEIPT,
                modifier=session.actor_id,
                category=category,
                entry_identity=identity,
            )
        return body

    return await asyncio.to_thread(_read_and_file)


async def file_produced(
    session: CompileSession, content: ContentStoreProtocol, data: bytes, *, origin: str, category: str, identity: str
) -> str:
    """File bytes the compiler produced (inline content, a built tree); returns the digest."""
    digest = digest_of(data)

    def _file() -> None:
        if not content.is_reachable(session.scope, digest):
            _store_produced(
                content,
                data,
                scope=session.scope,
                origin=origin,
                modifier=session.actor_id,
                category=category,
                entry_identity=identity,
            )

    await asyncio.to_thread(_file)
    return digest


def _store_produced(content: ContentStoreProtocol, data: bytes, **kwargs: Any) -> None:
    """File produced bytes; a store *refusal* is this entry's failure, never the whole compile's.

    ``ContentStoreError`` is the store's 4xx family (quota, scope, shape);
    its faults (corruption, I/O) are deliberately not caught — they are the
    platform's problem, and the run service records them as a failed apply.
    """
    try:
        content.store_produced(data, **kwargs)
    except ContentStoreError as exc:
        raise EntryFetchError(f"the platform could not file the entry's bytes: {exc}") from exc


async def file_tree(
    session: CompileSession,
    content: ContentStoreProtocol,
    delivery: Optional[EntryDelivery],
    tree: bytes,
    *,
    category: str,
    identity: str,
    content_type: Optional[str] = None,
) -> str:
    """File a tree the compiler built: the delivery's receipt, then reachability; returns the digest."""
    if delivery is not None:
        await asyncio.to_thread(
            delivery.file, session, tree, category=category, entry_identity=identity, content_type=content_type
        )
    return await file_produced(session, content, tree, origin=ORIGIN_TREE, category=category, identity=identity)
