"""Repository contracts for the Bot Genome Registry's five tables (RSI-03).

Every member is ``@abstractmethod``; domain imports are ``TYPE_CHECKING``-only
(see ``core/repository/README.md``). Every read and write is keyed by the bot
axes ``(env, entity_id, bot_id)``; the tenant half comes from the guard on the
models. A revision id, ref name or promotion id is never a key on its own: an
id from another bot resolves to ``None``.
"""
from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_genome.repository.models import (
        CandidateRefRequest,
        GenomePatchRow,
        GenomePromotionRow,
        GenomeRefEventRow,
        GenomeRefRow,
        GenomeRevisionRow,
    )


@runtime_checkable
class GenomeRevisionRepositoryProtocol(Protocol):
    """``ac_genome_revision``: insert-once rows whose only mutable columns are metadata."""

    @abstractmethod
    def insert(self, row: GenomeRevisionRow) -> tuple[GenomeRevisionRow, bool]:
        """Record a revision; ``(row, created)``.

        ``seq`` is assigned here (``MAX(seq) + 1`` for the bot, inside the
        insert transaction; a lost race retries). When the bot already holds
        ``row.revision_id`` the existing row comes back unchanged with
        ``created=False``: same id, same ``seq``, same parent.
        """
        ...

    @abstractmethod
    def record(
        self,
        row: GenomeRevisionRow,
        *,
        patch: Optional[GenomePatchRow] = None,
        candidate: Optional[CandidateRefRequest] = None,
        parent_from_latest: bool = False,
    ) -> tuple[GenomeRevisionRow, bool, Optional[GenomeRefRow]]:
        """``insert`` plus, in the same transaction, the patch row it came from and a run's candidate ref.

        ``(revision, created, ref)``. The patch row is written once per
        digest; the candidate ref is ``candidate/<run_id>/<n>`` with the
        next ``n`` for the run, or the existing ref already pointing at
        this revision (a retry). With ``parent_from_latest`` the new row's
        ``parent_id`` is the bot's latest revision as read inside the same
        transaction (``None`` for the first), so two overlapping records
        cannot both come out parentless. Either everything lands or nothing does.
        """
        ...

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str, revision_id: str
    ) -> Optional[GenomeRevisionRow]: ...

    @abstractmethod
    def get_by_seq(
        self, *, env: str, entity_id: str, bot_id: str, seq: int
    ) -> Optional[GenomeRevisionRow]: ...

    @abstractmethod
    def list(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        status: Optional[str] = None,
        parent_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[GenomeRevisionRow]]:
        """``(total, rows)`` newest ``seq`` first; ``status``/``parent_id`` filter when given."""
        ...

    @abstractmethod
    def update_metadata(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        revision_id: str,
        status: Optional[str] = None,
        evaluations: Optional[str] = None,
        annotations: Optional[str] = None,
    ) -> Optional[GenomeRevisionRow]:
        """Change the metadata outside the hash; a ``None`` argument leaves its column alone."""
        ...


@runtime_checkable
class GenomeRefRepositoryProtocol(Protocol):
    """``ac_genome_ref`` + ``ac_genome_ref_event``: names moved by compare-and-swap."""

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str, name: str
    ) -> Optional[GenomeRefRow]: ...

    @abstractmethod
    def list(
        self, *, env: str, entity_id: str, bot_id: str, name_prefix: Optional[str] = None
    ) -> list[GenomeRefRow]:
        """Every ref of the bot, by name; ``name_prefix`` narrows (``candidate/<run>/``)."""
        ...

    @abstractmethod
    def move(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        name: str,
        revision_id: str,
        expected_revision_id: Optional[str],
        moved_kind: str,
        moved_actor: str,
        run_id: Optional[str],
        reason: Optional[str],
        moved_at: datetime,
    ) -> Optional[GenomeRefRow]:
        """Compare-and-swap one ref; ``None`` on conflict.

        ``expected_revision_id=None`` means the ref must not exist yet (the
        insert loses to a concurrent creator); otherwise the UPDATE is
        conditioned on the ref pointing at it (rowcount semantics). A move
        appends one event row in the same transaction.
        """
        ...

    @abstractmethod
    def move_pair(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        revision_id: str,
        expected_active: Optional[str],
        moved_kind: str,
        moved_actor: str,
        run_id: Optional[str],
        reason: Optional[str],
        moved_at: datetime,
    ) -> Optional[tuple[GenomeRefRow, Optional[GenomeRefRow]]]:
        """Move ``active`` to ``revision_id`` and ``previous`` to where ``active`` was, in one transaction.

        ``(active, previous)`` on success; ``previous`` is ``None`` when there
        was no ``active`` before. ``None`` on a CAS conflict, with nothing
        moved. Two event rows.
        """
        ...

    @abstractmethod
    def candidate_count(
        self, *, env: str, entity_id: str, bot_id: str, run_id: str
    ) -> int:
        """How many ``candidate/<run_id>/<n>`` refs the bot has; the next ``n`` is one more."""
        ...

    @abstractmethod
    def events(
        self, *, env: str, entity_id: str, bot_id: str, name: str, limit: int = 50
    ) -> list[GenomeRefEventRow]:
        """One ref's moves, newest first."""
        ...


@runtime_checkable
class GenomePatchRepositoryProtocol(Protocol):
    """``ac_genome_patch``: content-addressed, one row per digest."""

    @abstractmethod
    def insert(self, row: GenomePatchRow) -> tuple[GenomePatchRow, bool]:
        """``(row, created)``; the existing row when the bot already holds the digest."""
        ...

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str, digest: str
    ) -> Optional[GenomePatchRow]: ...


@runtime_checkable
class GenomePromotionRepositoryProtocol(Protocol):
    """``ac_genome_promotion``: one row per promotion, written when it starts and when delivery answers."""

    @abstractmethod
    def insert(self, row: GenomePromotionRow) -> GenomePromotionRow: ...

    @abstractmethod
    def start(
        self,
        row: GenomePromotionRow,
        *,
        expected_active: Optional[str],
        expected_statuses: tuple[str, ...],
        moved_at: datetime,
    ) -> Optional[GenomePromotionRow]:
        """Move ``active``/``previous`` to ``row.revision_id`` by compare-and-swap, mark it ``promoted``, file ``row``.

        One transaction: a lost compare-and-swap, a revision whose status is
        no longer one of ``expected_statuses`` (``None`` either way) or a
        failed write leaves no ref moved, no status changed and no row.
        Re-promoting the current ``active`` leaves ``previous`` untouched.
        """
        ...

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str, promotion_id: str
    ) -> Optional[GenomePromotionRow]: ...

    @abstractmethod
    def list(
        self, *, env: str, entity_id: str, bot_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, list[GenomePromotionRow]]:
        """``(total, rows)`` newest first."""
        ...

    @abstractmethod
    def by_apply_reference(
        self, *, env: str, entity_id: str, bot_id: str, apply_reference: str
    ) -> Optional[GenomePromotionRow]:
        """The promotion whose delivery produced ``apply_reference`` (an apply id), or ``None``."""
        ...

    @abstractmethod
    def finish(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        promotion_id: str,
        status: str,
        apply_reference: Optional[str],
        apply_detail: str,
    ) -> Optional[GenomePromotionRow]:
        """Record delivery's answer; ``None`` when the row is gone."""
        ...
