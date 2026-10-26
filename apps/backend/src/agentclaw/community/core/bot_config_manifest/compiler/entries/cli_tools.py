"""``manifest.cli_tools`` → ``DesiredCliTool``: the tool's bytes fetched and filed, the install shape kept.

The digest belt is re-asked here (a stored document can predate the rule):
over the object store an entry must pin a ``digest``; over git the resolved
commit is the pin. A pinned digest already reachable for this bot is not
fetched again, which is what keeps a re-applied document from re-fetching
every binary. On the git road the composed ``subpath`` already selected the
one file, so the desired tool carries no subpath: the install shapes what it
is handed.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DesiredCliTool,
    EntryFailure,
    ContentRef,
    UnpackKind,
)
from agentclaw.community.core.bot_config_manifest.sources.source_resolver import (
    DeclaredSourceResolver,
    declared_protocol,
)
from agentclaw.community.core.apply_engine.cli_tools.declarations import declared_address
from agentclaw.community.core.bot_config_manifest.compiler.entries._fetch import (
    EntryFetchError,
    fetch,
    file_single,
)
from agentclaw.community.core.bot_config_manifest.compiler.session import CompileSession, digest_of
from agentclaw.community.core.bot_config_manifest.fetch.limits import FetchCategory
from agentclaw.community.core.apply_engine.support_matrix import SourceKind
from agentclaw.community.core.content_store import ContentStoreProtocol
from agentclaw.community.core.content_store.limits import DIGEST_RE

__all__ = ["compile_cli_tools"]

_CATEGORY = FetchCategory.CLI_TOOLS.value

_DIGEST_REQUIRED = (
    "cli_tools requires a 'digest' — the platform is distributing an executable, so the supply chain is pinned "
    "or the entry is refused"
)


async def compile_cli_tools(
    session: CompileSession,
    entries: Sequence[Mapping[str, Any]],
    *,
    resolver: DeclaredSourceResolver,
    content: ContentStoreProtocol,
) -> Declaration[DesiredCliTool]:
    desired: list[DesiredCliTool] = []
    failures: list[EntryFailure] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            failures.append(EntryFailure(f"[{index}]", "a cli_tools entry must be a mapping"))
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            failures.append(EntryFailure(f"[{index}]", "a cli_tools entry must name a command"))
            continue
        if name in seen:
            failures.append(EntryFailure(name, "declared more than once in this category"))
            continue
        seen.add(name)
        pin = entry.get("digest") or None
        if pin is not None and not (isinstance(pin, str) and DIGEST_RE.match(pin)):
            # The schema's own belt, re-asked: a stored document can predate it,
            # and a malformed pin must fail this entry, not the compile.
            failures.append(EntryFailure(name, "digest must be 'sha256:' followed by 64 lowercase hex characters"))
            continue
        protocol = declared_protocol(session, entry)
        if not pin and protocol is not SourceKind.GIT:
            failures.append(EntryFailure(name, _DIGEST_REQUIRED))
            continue
        if not entry.get("from") and not entry.get("source"):
            failures.append(EntryFailure(name, "a cli_tools entry must name a source"))
            continue
        unpack = entry.get("unpack")
        try:
            kind = UnpackKind(unpack) if unpack else None
        except ValueError:
            failures.append(EntryFailure(name, f"unknown unpack kind {unpack!r}"))
            continue
        subpath = entry.get("subpath") if isinstance(entry.get("subpath"), str) else None
        version = entry.get("version") if isinstance(entry.get("version"), str) else None
        address = declared_address(entry)

        if pin and await asyncio.to_thread(content.is_reachable, session.scope, pin):
            # The platform already holds these exact bytes for this bot: no fetch.
            desired.append(
                DesiredCliTool(
                    name,
                    ContentRef(pin),
                    pin=pin,
                    subpath=subpath,
                    unpack=kind,
                    version=version,
                    declared_address=address,
                )
            )
            continue
        try:
            delivery = await fetch(session, resolver, entry=entry, category=_CATEGORY, identity=name)
            data = await file_single(session, content, delivery, category=_CATEGORY, identity=name)
        except EntryFetchError as exc:
            failures.append(EntryFailure(name, exc.reason))
            continue
        # On a checkout ``single()`` already returned the one file the composed
        # subpath named, so the install must not look for an archive member.
        desired.append(
            DesiredCliTool(
                name,
                ContentRef(digest_of(data)),
                pin=pin,
                subpath=None if delivery.is_tree() else subpath,
                unpack=kind,
                version=version,
                note=delivery.note(),
                declared_address=address,
            )
        )
    return Declaration(tuple(desired), failures=tuple(failures))
