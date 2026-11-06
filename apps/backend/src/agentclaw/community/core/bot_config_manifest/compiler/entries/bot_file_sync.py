"""``manifest.resources`` entries named by a ``protocol: bot`` source → ``DesiredBotFileSync``.

A bot-source entry is written under ``manifest.resources`` (so the manifest
grammar stays one list for workspace files), but it is not a stored-content
resource: the compiler reclassifies it to the ``BOT_FILE_SYNC`` section, where
a per-engine sync materialiser converges it. ``compile_resources`` skips these
entries; this module collects them into the ``bot_file_sync`` declaration.

The entry's ``path`` (a literal the copy serializer writes, ``workspace/``) and
``excludes`` (literal relative paths, schema-validated) ride straight through.
``source_bot_id`` comes from the named ``protocol: bot`` source the entry
references with ``from``; the parser already validated it.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DesiredBotFileSync,
    EntryFailure,
)
from agentclaw.community.core.apply_engine.paths import resource_path_refusal
from agentclaw.community.core.apply_engine.support_matrix import SourceKind
from agentclaw.community.core.bot_config_manifest.compiler.entries._fetch import (
    EntryFetchError,
)
from agentclaw.community.core.bot_config_manifest.compiler.session import (
    CompileSession,
)
from agentclaw.community.core.bot_config_manifest.sources.source_resolver import (
    declared_protocol,
)
from agentclaw.community.core.bot_config_manifest.schema.sources import parse_source

__all__ = ["compile_bot_file_sync"]


def compile_bot_file_sync(
    session: CompileSession,
    entries: Sequence[Mapping[str, Any]] | None,
) -> Declaration[DesiredBotFileSync] | None:
    """Collect ``protocol: bot`` resources entries into the BOT_FILE_SYNC declaration.

    Returns ``None`` when the bot declared no resources or none of them name a
    bot source — the section stays undeclared, the same shape as a category the
    document does not mention, so the orchestrator's no-support path never
    walks it for an ordinary manifest apply.
    """
    if not entries:
        return None

    desired: list[DesiredBotFileSync] = []
    failures: list[EntryFailure] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            failures.append(EntryFailure(f"[{index}]", "a resources entry must be a mapping"))
            continue
        if declared_protocol(session, entry) is not SourceKind.BOT:
            # Stored-content entries belong to RESOURCES; only bot-source ones
            # are reclassified here. A mixed list walks both compilers; each
            # ignores the entries the other owns.
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            failures.append(EntryFailure(f"[{index}]", "a resources entry must declare a 'path'"))
            continue
        refusal = resource_path_refusal(path, what="path")
        if refusal is not None:
            failures.append(EntryFailure(path, refusal[1]))
            continue
        try:
            source_bot_id = _resolve_bot_id(session, entry)
        except EntryFetchError as exc:
            failures.append(EntryFailure(path, exc.reason))
            continue
        excludes = tuple(entry.get("excludes") or ())
        desired.append(
            DesiredBotFileSync(
                source_bot_id=source_bot_id,
                path=path,
                excludes=excludes,
            )
        )

    if not desired and not failures:
        return None
    return Declaration(tuple(desired), failures=tuple(failures))


def _resolve_bot_id(session: CompileSession, entry: Mapping[str, Any]) -> str:
    """The ``bot_id`` of the bot source an entry references.

    One road: a named ``from: <name>`` that points at a top-level
    ``protocol: bot`` source. An inline ``source: {protocol: bot, bot_id: …}``
    is parsed the same way. ``declared_protocol`` already verified the entry is
    a bot source, so the parse here is the trusted re-read for the ``bot_id``
    value — the same parser the schema and the resolver use, never a copy.
    """
    name = entry.get("from")
    if isinstance(name, str) and name:
        raw = session.source_session.sources.get(name)
        if raw is None:
            raise EntryFetchError(
                f"'from' references source {name!r}, which is not declared under 'sources'"
            )
    else:
        raw = entry.get("source")
    decl, violations = parse_source(raw)
    if decl is None or decl.bot_id is None:
        # The schema refuses this at PUT; reaching here means a document that
        # predates the rule or was stored by an older build. Surface it as an
        # entry failure rather than fetching.
        reason = "; ".join(v.message for v in violations) or "a bot source must declare 'bot_id'"
        raise EntryFetchError(reason)
    return decl.bot_id
