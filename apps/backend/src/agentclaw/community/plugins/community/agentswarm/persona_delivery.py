"""Persona content: stored by the platform, delivered to a worker after it boots.

The container platform does not materialize files — a persona is not something you
can hand to ``create_bot`` and have appear in the workspace. So delivery has two
steps, in this order:

1. **Store.** The persona's bytes go into the content-addressed store under the
   run's bot scope. Reference-counted by digest, so N personas with distinct content
   coexist, and two personas with identical content share one blob (correct for a
   content-addressed store, and the reason "distinct" is tested on different bytes).
2. **Deliver.** Once the worker's container is up, its identity file is written
   through the identity port, which is the only thing that reaches a live bot.

Why not the manifest seam: a bot's configuration document is **one** document per
bot. Storing persona B there overwrites persona A — the reason the swarm does not
use it (design D5).

Failure policy: a failed store or delivery fails **that persona**, not the run. The
spec requires it — one persona's content failing must not stop the others being
provisioned — and it matches the swarm's own retry model, which retries an attempt
rather than the slice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class _Content(Protocol):
    def store_produced(
        self, data: bytes, *, scope: Any, origin: str, modifier: str = "",
        category: str | None = None, entry_identity: str | None = None,
    ) -> Any: ...

    def read(self, digest: str) -> bytes: ...


class _IdentityFiles(Protocol):
    async def update_bot_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        content: str,
        operator_id: str,
        engine_type: str | None = None,
        *,
        stage: str = "draft",
    ) -> Any: ...


#: Where a persona's stored bytes came from, in the provenance log's vocabulary.
#: ``store_produced`` records ``produced://<origin>`` for both URLs.
PRODUCED_ORIGIN = "agentswarm/persona"
PRODUCED_CATEGORY = "identity"

#: The engines that name their persona file differently from the default.
_CLAUDE_CODE_IDENTITY = "CLAUDE.md"


def persona_file_type(*, engine_type: str, identity_file: str | None) -> str:
    """The file a persona lands in for this engine.

    An explicit ``identity_file`` on the spec wins — a caller that named a file knows
    which one its engine reads. Otherwise the engine's own convention decides, and
    the swarm never invents a fourth name: an unknown engine gets the default the
    platform's own docs use.
    """
    if identity_file and identity_file.strip():
        return identity_file.strip()
    if (engine_type or "").strip() == "claude_code":
        return _CLAUDE_CODE_IDENTITY
    return "SOUL.md"


@dataclass(frozen=True)
class PersonaStored:
    """A persona's content, filed and addressable."""

    digest: str
    file_type: str
    size_bytes: int


def store_persona(
    *,
    content: _Content,
    scope: Any,
    instructions: str,
    engine_type: str,
    identity_file: str | None,
    modifier: str,
    persona_key: str,
) -> PersonaStored:
    """File a persona's content and return what delivery needs to fetch it.

    Empty instructions are stored as empty bytes rather than skipped: a persona that
    declares nothing must land an **empty** file, not a stale one from a previous
    run, and the store's digest makes that explicit.
    """
    body = (instructions or "").encode("utf-8")
    file_type = persona_file_type(
        engine_type=engine_type, identity_file=identity_file
    )
    record = content.store_produced(
        body,
        scope=scope,
        origin=PRODUCED_ORIGIN,
        modifier=modifier,
        category=PRODUCED_CATEGORY,
        entry_identity=f"{persona_key}:{file_type}",
    )
    return PersonaStored(
        digest=record.digest, file_type=file_type, size_bytes=len(body)
    )


async def deliver_persona(
    *,
    content: _Content,
    identity_files: _IdentityFiles,
    stored: PersonaStored,
    entity_type: str,
    entity_id: str,
    worker_id: str,
    owner_id: str,
    engine_type: str,
) -> None:
    """Write a stored persona into its worker's running container.

    Reads the bytes back from the store rather than trusting the local copy: the
    digest is the contract, and a materialiser handed bytes it did not read is
    asserting something it cannot prove.

    Raises on failure — the caller decides whether that fails the persona or only
    reports it. The swarm fails the persona.
    """
    body = content.read(stored.digest).decode("utf-8")
    await identity_files.update_bot_file(
        entity_type,
        entity_id,
        worker_id,
        stored.file_type,
        body,
        owner_id,
        engine_type,
        stage="draft",
    )