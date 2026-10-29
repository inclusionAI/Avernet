"""Best-effort projection of the Bot's current knowledge selection into AGENTS.md.

The binding table (``bot_knowledge_binding``) is the source of truth for which
knowledge bases a Bot has enabled. The Bot's runtime, however, only reads what
its container carries — ``AGENTS.md`` above all — and knows nothing about that
table. This module projects the current selection into a fenced section of the
Bot's draft ``AGENTS.md`` so the running agent can see which knowledge bases it
should address and with which tools.

The mechanism mirrors ``IdentityService.sync_agents_md``'s reference-file
section: a stable pair of HTML comment markers fences the generated text, the
section is rebuilt whole from the current selection (never edited in place),
and the sync itself is best-effort — a failure logs a warning and leaves the
stored selection, and the HTTP response that triggered the sync, untouched.

Marker discipline: the markers below are the only anchor for locating an
existing section. They must never change once shipped — renaming them would
orphan every section already written into existing bots' AGENTS.md files, and
the next sync would append a second, stale copy instead of replacing the old
one.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agentclaw.community.core.bot_knowledge.repository.store import (
    BotKnowledgeRepository,
)
from agentclaw.community.core.knowledge_management.ports import KnowledgeCatalog
from agentclaw.community.kernel.knowledge import BotKnowledgeScope
from agentclaw.community.log import get_logger

if TYPE_CHECKING:  # pragma: no cover — identity.py reaches the DI graph at module scope
    from agentclaw.community.core.services.identity import IdentityService

logger = get_logger()

AGENTS_MD_FILE = "AGENTS.md"

KNOWLEDGE_SECTION_START_MARKER = "<!-- KNOWLEDGE_TOOLS_SECTION_START -->"
KNOWLEDGE_SECTION_END_MARKER = "<!-- KNOWLEDGE_TOOLS_SECTION_END -->"

# ``KnowledgeCatalog.details`` reads one ordered, complete batch of up to 20
# unique IDs; any denied item fails its whole batch.
_DETAILS_BATCH_SIZE = 20


def build_knowledge_section(entries: list[tuple[str, str | None]]) -> str:
    """Render the fenced section body for the given ``(kb_id, name)`` pairs.

    The returned text runs from the start-marker line to the end-marker line
    with no leading or trailing blank lines; placement is the caller's job.
    """
    lines = [
        KNOWLEDGE_SECTION_START_MARKER,
        "",
        "## Knowledge Base Tools",
        "",
        "You can retrieve knowledge via the TeamClaw Knowledge MCP tools. "
        "The knowledge bases currently enabled for this Bot are:",
        "",
    ]
    for kb_id, name in entries:
        lines.append(f"- `{kb_id}` — {name}" if name else f"- `{kb_id}`")
    lines += [
        "",
        "Usage:",
        "",
        "- Use `search_knowledge` to retrieve knowledge (pass one of the "
        "`kb_id` values above); use `read_document` to read the full text "
        "of a matched document.",
        "",
        KNOWLEDGE_SECTION_END_MARKER,
    ]
    return "\n".join(lines)


def _locate_section(content: str) -> tuple[int, int] | None:
    """Fence seams: the earliest END, paired with the START just before it.

    A stray marker pasted into hand-written text (a user quoting the section
    header for notes) must never widen the replaced range. Pairing the
    earliest END with the *closest* preceding START keeps the cut inside the
    real section; a file whose stray markers cannot close into a pair is
    refused outright, so the caller skips the update instead of touching the
    file.
    """
    end_pos = content.find(KNOWLEDGE_SECTION_END_MARKER)
    if end_pos == -1:
        return None
    start = content.rfind(KNOWLEDGE_SECTION_START_MARKER, 0, end_pos)
    if start == -1:
        return None
    return start, end_pos + len(KNOWLEDGE_SECTION_END_MARKER)


def apply_knowledge_section(content: str, section: str | None) -> str | None:
    """Insert, replace, or remove the fenced section in ``content``.

    - ``section`` given, no closable marker pair → append at the end of the
      file (an empty file gets a minimal ``# AGENTS.md`` heading first); a
      stray START without any END also counts as "no section" for reading
      but the write is refused (below), never appended.
    - ``section`` given, closable marker pair → in-place replacement that
      keeps the section's position; everything outside the fence is carried
      through byte-for-byte.
    - ``section`` ``None`` → remove the section; without a start marker the
      content is returned unchanged.
    - stray markers that cannot close into a pair (orphan START, orphan END,
      an END ahead of any START) → ``None``: the caller must skip rather than
      risk eating hand-written content.
    """
    seams = _locate_section(content)
    if seams is None:
        if section is None:
            return content
        if KNOWLEDGE_SECTION_START_MARKER in content:
            return None
        body = content.rstrip("\n")
        if not body:
            return f"# AGENTS.md\n\n{section}\n"
        return f"{body}\n\n{section}\n"
    start, end = seams
    if section is None:
        while end < len(content) and content[end] == "\n":
            end += 1
        before = content[:start].rstrip("\n")
        after = content[end:].lstrip("\n")
        if not after:
            return f"{before}\n" if before else ""
        if before:
            return f"{before}\n\n{after}"
        return after
    # Replace in place: cut precisely at the marker seams and keep whatever
    # newline structure the surrounding hand-written text already has.
    return content[:start] + section + content[end:]


class BotKnowledgeAgentsMdSync:
    """Project the current selection into the Bot's draft ``AGENTS.md``.

    Constructed explicitly by the DI provider (the ``IdentityService`` import
    stays function-level there, mirroring the identity↔harness import-cycle
    discipline), which is why this class carries no ``@inject`` of its own.
    """

    def __init__(
        self,
        repository: BotKnowledgeRepository,
        catalog: KnowledgeCatalog,
        identity: IdentityService,
    ) -> None:
        self.repository = repository
        self.catalog = catalog
        self.identity = identity
        # One lock per (bot, owner, env). Sync is a read-modify-write of the
        # whole AGENTS.md; two enablement responses landing concurrently must
        # not interleave it, or the later stale read can overwrite the earlier
        # write (still self-healing on the next enablement, but needlessly).
        # Process-local only — across workers the whole-rebuild idempotence
        # remains the safety net.
        self._locks: dict[str, asyncio.Lock] = {}

    async def sync(self, *, scope: BotKnowledgeScope, actor_id: str) -> None:
        """Rewrite the fenced section from the stored selection; never raises."""
        lock = self._locks.setdefault(
            f"{scope.bot_id}|{scope.owner_id}|{scope.env}", asyncio.Lock()
        )
        async with lock:
            try:
                await self._sync(scope=scope, actor_id=actor_id)
            except Exception:
                logger.warning(
                    "[knowledge-agents-md] sync failed bot=%s owner=%s env=%s",
                    scope.bot_id,
                    scope.owner_id,
                    scope.env,
                    exc_info=True,
                )

    async def _sync(self, *, scope: BotKnowledgeScope, actor_id: str) -> None:
        bot = self.repository.get_bot(scope)
        enabled = self.repository.list_enabled_current(scope)
        names = self._resolve_names(
            space_id=bot["space_id"], kb_ids=list(enabled), actor_id=actor_id
        )
        content = await self.identity.read_identity_file(
            bot["entity_type"],
            bot["entity_id"],
            scope.bot_id,
            AGENTS_MD_FILE,
            bot["owner_id"],
        )
        section = (
            build_knowledge_section([(kb_id, names.get(kb_id)) for kb_id in enabled])
            if enabled
            else None
        )
        new_content = apply_knowledge_section(content or "", section)
        if new_content is None:
            logger.error(
                "[knowledge-agents-md] broken section fence, skipping update "
                "bot=%s owner=%s env=%s",
                scope.bot_id,
                scope.owner_id,
                scope.env,
            )
            return
        if new_content == (content or ""):
            return
        await self.identity.write_identity_file(
            bot["entity_type"],
            bot["entity_id"],
            scope.bot_id,
            AGENTS_MD_FILE,
            new_content,
            bot["owner_id"],
        )
        logger.info(
            "[knowledge-agents-md] updated AGENTS.md bot=%s owner=%s env=%s enabled=%d",
            scope.bot_id,
            scope.owner_id,
            scope.env,
            len(enabled),
        )

    def _resolve_names(
        self, *, space_id: int, kb_ids: list[str], actor_id: str
    ) -> dict[str, str]:
        """Best-effort display names; a failed batch degrades to ids only."""
        names: dict[str, str] = {}
        for offset in range(0, len(kb_ids), _DETAILS_BATCH_SIZE):
            batch = kb_ids[offset : offset + _DETAILS_BATCH_SIZE]
            try:
                details = self.catalog.details(
                    space_id=space_id, kb_ids=list(batch), actor_id=actor_id
                )
            except Exception:
                logger.warning(
                    "[knowledge-agents-md] catalog details failed, ids only "
                    "space=%s count=%d",
                    space_id,
                    len(batch),
                    exc_info=True,
                )
                continue
            if not isinstance(details, list) or len(details) != len(batch):
                continue
            for item in details:
                if not isinstance(item, dict):
                    continue
                kb_id, name = item.get("kb_id"), item.get("name")
                if type(kb_id) is str and type(name) is str and name:
                    names[kb_id] = name
        return names


__all__ = [
    "AGENTS_MD_FILE",
    "BotKnowledgeAgentsMdSync",
    "KNOWLEDGE_SECTION_END_MARKER",
    "KNOWLEDGE_SECTION_START_MARKER",
    "apply_knowledge_section",
    "build_knowledge_section",
]
