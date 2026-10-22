"""Service API of the Genome Registry (RSI-03), re-exported by ``api/bot_genome_registry_service.py``.

Every call names the bot (:class:`BotRef`); a revision id, sequence number
or ref name from another bot resolves to :class:`RevisionNotFound`.
``capture`` and ``record_patch`` are ``async`` because the state reader and
the delivery-side checks are.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.bot_genome.models import (
    BotRef,
    CompiledManifest,
    ContentReceipt,
    GenomeDiff,
    GenomeRef,
    GenomeRevision,
    Provenance,
    RecordedRevision,
    RefMover,
    RefUpdate,
    RevisionPage,
    RevisionStatus,
    RunScope,
)


@runtime_checkable
class GenomeRegistryProtocol(Protocol):
    """Revisions, refs, patches, diffs, content and the compiled document of one bot."""

    @abstractmethod
    def get_revision(self, bot: BotRef, rev: str) -> GenomeRevision:
        """``rev`` is a revision id, ``r<seq>`` or a ref name. Raises ``RevisionNotFound``."""
        ...

    @abstractmethod
    def list_revisions(
        self,
        bot: BotRef,
        *,
        status: RevisionStatus | None = None,
        parent: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> RevisionPage:
        """Metadata only, newest ``seq`` first."""
        ...

    @abstractmethod
    async def capture(self, bot: BotRef, *, created_by: Provenance) -> RecordedRevision:
        """Record the bot's current state; parent ``active`` or none; policy copied from ``active``.

        Idempotent: unchanged state returns the existing revision with
        ``created=False``. Raises ``CaptureRefused`` naming what cannot be
        recorded.
        """
        ...

    @abstractmethod
    async def record_patch(
        self,
        bot: BotRef,
        patch: dict[str, Any],
        *,
        created_by: Provenance,
        status: RevisionStatus,
        run: RunScope | None = None,
    ) -> RecordedRevision:
        """Apply ``patch`` to its own ``base`` and record the result (README §6.4).

        ``status`` is ``DRAFT`` (an owner) or ``CANDIDATE`` (a run, which
        also names ``candidate/<run>/<n>``). Raises ``RevisionNotFound``
        for a base that is not this bot's, ``PatchRejected`` with every
        reason, ``LimitExceeded``, ``ContentNotReachable``.
        """
        ...

    @abstractmethod
    def set_policy(self, bot: BotRef, policy: dict[str, Any], *, created_by: Provenance) -> RecordedRevision:
        """Record a revision with ``active``'s spec and the new policy.

        The spec is re-checked against what apply accepts now; ``PolicyRejected``
        carries the policy's own reasons or the spec's.
        """
        ...

    @abstractmethod
    def set_status(
        self, bot: BotRef, revision_id: str, status: RevisionStatus, *, actor: Provenance, reason: str
    ) -> None:
        """Metadata only; the content never changes."""
        ...

    @abstractmethod
    def diff(self, bot: BotRef, rev: str, against: str) -> GenomeDiff: ...

    @abstractmethod
    def list_refs(self, bot: BotRef) -> list[GenomeRef]: ...

    @abstractmethod
    def move_ref(self, bot: BotRef, update: RefUpdate, *, actor: Provenance, mover: RefMover) -> GenomeRef:
        """Compare-and-swap one ref. Raises ``MoverNotAllowed``, ``RevisionNotFound``, ``RefConflict``."""
        ...

    @abstractmethod
    def read_content(self, bot: BotRef, digest: str) -> bytes:
        """Bytes the bot can reach, hash-verified. Raises ``ContentNotReachable``."""
        ...

    @abstractmethod
    def put_content(self, bot: BotRef, data: bytes, *, provenance: Provenance) -> ContentReceipt:
        """Store uploaded bytes for a later patch. Raises ``LimitExceeded``."""
        ...

    @abstractmethod
    def compile(self, bot: BotRef, rev: str) -> CompiledManifest: ...

    @abstractmethod
    def applied_revision(self, bot: BotRef) -> str | None:
        """The revision whose promotion started the bot's last apply, or ``None``.

        Only a ``SUCCEEDED`` apply counts: a running, partial or failed one
        means the bot does not run that revision, so it reads as drift.
        """
        ...
