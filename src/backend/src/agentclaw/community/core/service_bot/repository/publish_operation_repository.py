"""Publish operation ledger repository — abstract base class.

Defines data access for ``ac_publish_operation`` (the crash-safe operation
ledger). The concrete unified ORM implementation
(``plugins.publish_operation_repository.OrmPublishOperationRepository``) subclasses
this ABC and runs on both prod OceanBase and local SQLite via the injected
``DatabasePlugin``.

State transitions are single optimistic-lock UPDATEs (``WHERE id=? AND
state=?``) — the same CAS idiom as ``BotPublishRepository.update_status`` — so a
transition that loses the race (wrong source state) returns ``None`` rather than
clobbering. Field writes that carry no state change (``update_result``) are blind
column overwrites within a held operation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationRecord,
)


class PublishOperationRepository(ABC):
    """Data access for the publish operation ledger."""

    # ── insert ──────────────────────────────────────────────────────────
    @abstractmethod
    def insert(self, data: Dict[str, Any]) -> PublishOperationRecord:
        """Persist a new ``PENDING`` intent row and return it.

        ``data`` carries: ``publish_id``, ``operation_kind``, ``stage``,
        ``attempt`` (default 1), ``request_id``, ``operator``, and optionally
        ``bot_uuid`` / ``params`` / ``state`` / ``baas_publish_id`` / ``env``.
        Conflicts on the operation-identity unique index are the caller's
        responsibility to avoid (the runner's ``open_operation`` does
        get-or-insert).
        """

    # ── queries ─────────────────────────────────────────────────────────
    @abstractmethod
    def get_by_id(self, op_id: int) -> Optional[PublishOperationRecord]:
        """Return the operation by id, or ``None``."""

    @abstractmethod
    def get_by_key(
        self,
        publish_id: int,
        operation_kind: str,
        stage: str,
        attempt: int,
    ) -> Optional[PublishOperationRecord]:
        """Return the operation with this exact identity, or ``None``."""

    @abstractmethod
    def get_latest_by_kind(
        self,
        publish_id: int,
        operation_kind: str,
        stage: str,
    ) -> Optional[PublishOperationRecord]:
        """Return the highest-``attempt`` operation of this kind/stage, or
        ``None`` — the row a re-entry (retry / restart / progress read) resumes.
        """

    @abstractmethod
    def list_by_publish_id(self, publish_id: int) -> List[PublishOperationRecord]:
        """Return every operation row for a publish record (any state)."""

    @abstractmethod
    def list_by_bot(self, bot_uuid: str, env: str) -> List[PublishOperationRecord]:
        """Return every operation row targeting ``bot_uuid`` in ``env`` — the
        ledger-known-ids side of adopt-by-query differencing."""

    @abstractmethod
    def max_attempt(
        self,
        publish_id: int,
        operation_kind: str,
        stage: str,
    ) -> int:
        """Return the highest ``attempt`` for this kind/stage (0 if none) — so a
        reissue after ``abandon`` opens ``attempt + 1``."""

    # ── CAS state transitions ───────────────────────────────────────────
    @abstractmethod
    def record_workflow(
        self,
        op_id: int,
        *,
        baas_publish_id: int,
        bot_uuid: Optional[str] = None,
    ) -> Optional[PublishOperationRecord]:
        """Atomically persist the BaaS workflow id and flip
        ``PENDING -> ID_RECORDED`` (and ``bot_uuid`` when a creation resolved
        it). Returns ``None`` if the row was not ``PENDING`` (lost the CAS)."""

    @abstractmethod
    def complete(self, op_id: int) -> Optional[PublishOperationRecord]:
        """CAS ``ID_RECORDED`` -> ``COMPLETED``. ``None`` if not ``ID_RECORDED``."""

    @abstractmethod
    def complete_without_workflow(
        self, op_id: int
    ) -> Optional[PublishOperationRecord]:
        """CAS ``PENDING`` -> ``COMPLETED`` for a non-BaaS operation.

        Local operations such as an ARCA draft restore have no external workflow
        id to record, but still need a terminal ledger row with full attempt and
        timing history.
        """

    @abstractmethod
    def fail(self, op_id: int, error: str) -> Optional[PublishOperationRecord]:
        """Mark a non-terminal operation ``FAILED`` with ``error``. ``None`` if
        already terminal."""

    @abstractmethod
    def abandon(self, op_id: int, reason: str) -> Optional[PublishOperationRecord]:
        """Mark a non-terminal operation ``ABANDONED`` (superseded). ``None`` if
        already terminal."""

    @abstractmethod
    def fail_by_workflow(
        self,
        publish_id: int,
        baas_publish_id: int,
        error: str,
    ) -> bool:
        """Outcome-correct the op carrying this BaaS workflow to ``FAILED``.

        The op's own steps may have completed long before its BaaS workflow
        reaches a terminal state — ``COMPLETED`` means "bookkeeping done", not
        "deploy landed". When the progress sync observes the workflow FAILED,
        this write records that outcome on the ledger row so liveness readers
        (``is_current_online_deployment`` and its superseded scan) stop
        treating the deploy as landed. Permits ``ID_RECORDED -> FAILED`` and —
        deliberately, unlike :meth:`fail` — ``COMPLETED -> FAILED``.

        Returns ``True`` if a row was corrected; ``False`` when no row of this
        publish carries ``baas_publish_id`` (e.g. pre-ledger records) or the
        matching row is already ``FAILED``/``ABANDONED``."""

    # ── field updates (within a held operation; no state change) ────────
    @abstractmethod
    def update_result(
        self,
        op_id: int,
        result: Dict[str, Any],
    ) -> Optional[PublishOperationRecord]:
        """Blind-overwrite the ``result`` JSON (caller does read-modify-write).
        Records step outputs (binding id, draft id, puid). ``None`` if absent."""
