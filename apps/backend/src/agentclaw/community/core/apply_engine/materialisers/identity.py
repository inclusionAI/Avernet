"""``identity`` → ``IdentityService`` — the file set, minus the reserved names.

**The entry shape.** ``identity`` for this category is the entry's ``type``,
which is a whitelisted **filename** — ``SOUL.md``, ``RULES.md``, ``OKR.md``
and the rest of ``VALID_IDENTITY_FILES`` — not a free-form label. Both an
inline body and a fetched one are accepted::

    manifest:
      identity:
        # inline content: no fetch at all
        - type: RULES.md
          content: |
            # rules

        # an inline source: a declaration object, like every source
        - type: SOUL.md
          source:
            protocol: oss
            bucket: team-artifacts
            key: identity/soul.md
            auth: oss-prod

        # a named source: the entry's subpath must name ONE file, because
        # this category delivers a single body per entry
        - type: OKR.md
          from: content
          subpath: okr.md

An entry reaches ``resolve`` as the raw mapping, e.g. ``{"type": "SOUL.md",
"source": {"protocol": "oss", "bucket": "team-artifacts",
"key": "identity/soul.md", "auth": "oss-prod"}}``.

The area this overwrites is the bot's identity
file set, minus ``MEMORY.md`` / ``IDENTITY.md`` — engine-generated runtime
state that apply never writes and never removes, whatever a document says.
The validator refuses their *declaration*; this module refuses to reach them
from the removal side too, which is the half the validator cannot see, so the
guarantee holds even for a document that arrives without the validator in its
history (a hand-built apply in W8's lifecycle points).

**Same write path as the public API, by reuse rather than imitation.** The
materialiser derives the entity pair exactly as the identity router does —
``identity_coords_from_record`` — whose stated reason for living in core is
"so manifest apply addresses identity the same way" (the W10 seam), and then
calls ``update_bot_file`` / ``read_identity_file`` / ``list_bot_files`` — the
same methods the identity router's GET/PUT/list call. A materialiser writing
through its own path would be a second, drifting answer to "where does an
identity file live".

"Removal" is an empty write, deliberately: the domain's own contract is that
absent and empty are one state (``read_identity_file`` answers "" for a
missing file; ``list_bot_files`` reports ``exists`` as ``bool(content)``).
IdentityService exposes no delete, and inventing one in apply would give the
identity area two removal semantics where every reader of the area sees one.
A reserved name never receives even an empty write.

Fetch (for ``source`` entries — a declared ``source``, or a ``from`` name,
both through W7's declared-source door) happens in ``resolve`` through
:class:`~agentclaw.community.core.bot_config_manifest.sources.source_resolver.DeclaredSourceResolver`;
a failure aborts the whole category before the first write — §3.2's
all-or-nothing, by construction, never by discipline. The bytes are decoded
to text here rather than in the fetch pipeline because "is this UTF-8" is a
property only this category has an opinion about.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional, Sequence

from agentclaw.community.core.content_store import ContentReader
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.desired_state import DesiredIdentityFile
from agentclaw.community.core.content_store.errors import ContentStoreError
from agentclaw.community.core.apply_engine.outcomes import (
    EntryOutcome,
    EntryResult,
)
from agentclaw.community.core.apply_engine.registry import (
    CategoryPlan,
    Intent,
    Materialiser,
    PlannedEntry,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.apply_engine.capabilities import (
    Category,
)
from agentclaw.community.core.apply_engine.identity_types import (
    RESERVED_IDENTITY_FILES,
    legal_identity_types,
)

def _decode_utf8(body: bytes) -> Optional[str]:
    """Both roads hand back bytes; only this category decides they must be
    text (the module docstring's ruling). ``None`` means "not UTF-8" -- the
    two call sites turn that into the same refusal, so the message is not
    the thing that can drift between them."""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


class IdentityMaterialiser(Materialiser):
    """Converges the bot's identity file set toward the declaration.

    ``identity`` is the entry's ``type``; ``Intent.value`` is the decoded
    **text** body, a ``str``, not bytes — this is the one category that
    insists its bytes are UTF-8::

        resolve -> ResolveResult(intents=(Intent(
                       identity="SOUL.md", value="# who I am\n"),))
        plan    -> CategoryPlan(
                       entries=(PlannedEntry(<that intent>, "updated"),),
                       removals=("OKR.md",))
        write   -> (EntryResult(Category.IDENTITY, "SOUL.md",
                                EntryOutcome.UPDATED),)

    A removal is an **empty write**, not a delete: the domain reads absent and
    empty as one state, and ``IdentityService`` exposes no delete. A reserved
    name (``MEMORY.md``, ``IDENTITY.md``) never receives even that.
    """

    construct = Category.IDENTITY

    def __init__(self, identity_service: Any, content: ContentReader) -> None:
        self._identity = identity_service
        self._content = content

    async def resolve(self, ctx: ApplyContext, entries: Sequence[DesiredIdentityFile]) -> ResolveResult:
        """Desired entries → intents: one text body per file, read from the store.

        The engine's legal set is **re-asked here** rather than trusted from
        the ``PUT`` that accepted the document: a bot's engine can change
        after a document is stored, and claude_code's legal set is one file
        long. The reserved names are refused even though the engine's legal
        set admits them — the belt for a document that never met the
        validator.
        """
        intents: list[Intent] = []
        failures: list[ResolveFailure] = []
        legal = legal_identity_types(ctx.engine_type)

        for entry in entries:
            file_type = entry.type
            if file_type in RESERVED_IDENTITY_FILES:
                failures.append(
                    ResolveFailure(
                        file_type,
                        f"{file_type} is engine-generated runtime state: apply "
                        "never writes it and never removes it",
                    )
                )
                continue
            if file_type not in legal:
                failures.append(
                    ResolveFailure(
                        file_type,
                        f"identity type {file_type!r} is not valid for engine "
                        f"{ctx.engine_type!r}; allowed: " + ", ".join(sorted(legal)),
                    )
                )
                continue
            try:
                body = await asyncio.to_thread(self._content.read, entry.content.digest)
            except ContentStoreError as exc:
                failures.append(ResolveFailure(file_type, f"the identity file's bytes are not in the store: {exc}"))
                continue
            text = _decode_utf8(body)
            if text is None:
                failures.append(
                    ResolveFailure(
                        file_type,
                        "the identity source is not UTF-8 text",
                    )
                )
                continue
            intents.append(Intent(file_type, text, note=entry.note))

        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """Classify each intent against the file set; compute removals."""
        entity_type, entity_id = _coords(ctx)
        presence = dict(
            await self._identity.list_bot_files(
                entity_type, entity_id, ctx.bot_id, ctx.owner_id
            )
        )
        declared = {intent.identity for intent in intents}

        planned = []
        for intent in intents:
            current = ""
            if presence.get(intent.identity):
                current = await self._identity.read_identity_file(
                    entity_type, entity_id, ctx.bot_id, intent.identity, ctx.owner_id
                )
            if not current:
                outcome = EntryOutcome.CREATED.value
            elif current == intent.value:
                outcome = EntryOutcome.UNCHANGED.value
            else:
                outcome = EntryOutcome.UPDATED.value
            planned.append(PlannedEntry(intent, outcome))

        # The area: files that exist, minus the reserved names — never removal
        # candidates, never emptied, whatever the document does or omits.
        existing = {
            file_type
            for file_type, exists in presence.items()
            if exists and file_type not in RESERVED_IDENTITY_FILES
        }
        removals = tuple(sorted(existing - declared))
        return CategoryPlan(entries=tuple(planned), removals=removals)

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Execute the plan: write each changed file, empty each removal.

        An ``unchanged`` entry calls nothing — that absence is what the
        convergence criterion observes. Removal as an empty write is the
        domain's own "absent" (see the module docstring), issued through the
        same write path the routers use.
        """
        entity_type, entity_id = _coords(ctx)
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
            await self._identity.update_bot_file(
                entity_type,
                entity_id,
                ctx.bot_id,
                planned.intent.identity,
                planned.intent.value,
                # The actor, never the addressed owner: attributing a
                # collaborator's change to the bot's owner is the one thing
                # an audit column must not do.
                ctx.actor_id,
            )
            results.append(
                EntryResult(
                    self.construct,
                    planned.intent.identity,
                    EntryOutcome(planned.outcome),
                    # A keep_last fallback is a fact the report must state:
                    # the file's content says "what", this says "why it is
                    # the platform's copy and not the source's".
                    note=planned.intent.note,
                )
            )
        for file_type in plan.removals:
            await self._identity.update_bot_file(
                entity_type, entity_id, ctx.bot_id, file_type, "", ctx.actor_id
            )
        return tuple(results)


def _coords(ctx: ApplyContext) -> tuple[str, str]:
    """The entity pair every identity addressing uses, the router's own way.

    ``identity_coords_from_record`` is imported lazily for the cycle reason
    ``schema.entries`` records for the same function it wraps: the identity
    service module pulls in the device dispatcher at import, and this package
    must not drag that graph into importers that only walk manifest rules.
    """
    from agentclaw.community.core.services.identity import (
        identity_coords_from_record,
    )

    coords = identity_coords_from_record(ctx.bot_id, ctx.owner_id)
    return coords.entity_type, coords.entity_id


__all__ = ["IdentityMaterialiser"]
