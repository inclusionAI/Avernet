"""Publish operation ledger repository protocol — business-layer abstraction.

Defines data access for ``ac_publish_operation`` (the crash-safe operation
ledger). The concrete unified ORM implementation
(``plugins.publish_operation_repository.PublishOperationRepository``) satisfies
this Protocol structurally and runs on both prod OceanBase and local SQLite via
the injected ``DatabasePlugin``.

State transitions are single optimistic-lock UPDATEs (``WHERE id=? AND
state=?``) — the same CAS idiom as ``BotPublishRepository.update_status`` — so a
transition that loses the race (wrong source state) returns ``None`` rather than
clobbering. Field writes that carry no state change (``update_result`` /
``update_error``) are blind column overwrites within a held operation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationRecord,
)


class PublishOperationRepositoryProtocol(Protocol):
    """Data access for the publish operation ledger."""

    # ── insert ──────────────────────────────────────────────────────────
    def insert(self, data: Dict[str, Any]) -> PublishOperationRecord:
        """Persist a new ``PENDING`` intent row and return it.

        ``data`` carries: ``publish_id``, ``operation_kind``, ``stage``,
        ``attempt`` (default 1), ``request_id``, ``operator``, and optionally
        ``bot_uuid`` / ``params`` / ``state`` / ``baas_publish_id`` / ``env``.
        Conflicts on ``uk_op`` are the caller's responsibility to avoid (the
        runner's ``open_operation`` does get-or-insert).
        """
        ...

    # ── queries ─────────────────────────────────────────────────────────
    def get_by_id(self, op_id: int) -> Optional[PublishOperationRecord]:
        """Return the operation by id, or ``None``."""
        ...

    def get_by_key(
        self,
        publish_id: int,
        operation_kind: str,
        stage: str,
        attempt: int,
    ) -> Optional[PublishOperationRecord]:
        """Return the operation with this exact identity, or ``None``."""
        ...

    def get_latest_by_kind(
        self,
        publish_id: int,
        operation_kind: str,
        stage: str,
    ) -> Optional[PublishOperationRecord]:
        """Return the highest-``attempt`` operation of this kind/stage, or
        ``None`` — the row a re-entry (retry / restart / progress read) resumes.
        """
        ...

    def list_by_publish(self, publish_id: int) -> List[PublishOperationRecord]:
        """Return every operation row for a publish record (any state)."""
        ...

    def list_by_bot(self, bot_uuid: str, env: str) -> List[PublishOperationRecord]:
        """Return every operation row targeting ``bot_uuid`` in ``env`` — the
        ledger-known-ids side of adopt-by-query differencing."""
        ...

    def max_attempt(
        self,
        publish_id: int,
        operation_kind: str,
        stage: str,
    ) -> int:
        """Return the highest ``attempt`` for this kind/stage (0 if none) — so a
        reissue after ``abandon`` opens ``attempt + 1``."""
        ...

    # ── CAS state transitions ───────────────────────────────────────────
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
        ...

    def complete(self, op_id: int) -> Optional[PublishOperationRecord]:
        """CAS ``ID_RECORDED -> COMPLETED``. ``None`` if not ``ID_RECORDED``."""
        ...

    def fail(self, op_id: int, error: str) -> Optional[PublishOperationRecord]:
        """Mark a non-terminal operation ``FAILED`` with ``error``. ``None`` if
        already terminal."""
        ...

    def abandon(self, op_id: int, reason: str) -> Optional[PublishOperationRecord]:
        """Mark a non-terminal operation ``ABANDONED`` (superseded). ``None`` if
        already terminal."""
        ...

    # ── field updates (within a held operation; no state change) ────────
    def update_result(
        self,
        op_id: int,
        result: Dict[str, Any],
    ) -> Optional[PublishOperationRecord]:
        """Blind-overwrite the ``result`` JSON (caller does read-modify-write).
        Records step outputs (binding id, draft id, puid). ``None`` if absent."""
        ...
