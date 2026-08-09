"""Repository contracts owned by the ``publishing`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishOperationRecord


class BotPublishRepositoryProtocol(Protocol):
    """Bot publish-record data-access interface.

    All methods take/return dicts or Pydantic models; the concrete implementation
    is provided by plugins.

    Implementation: a single unified ORM body
    (plugins.bot_publish_repository.BotPublishRepository) runs on
    both prod OceanBase and local SQLite via the injected
    DatabasePlugin.
    """

    # ========================================================================
    # Insert Operations
    # ========================================================================

    @abstractmethod
    def insert(self, data: Dict[str, Any]) -> BotPublishRecord:
        """Create a publish record.

        Args:
            data: dict of publish-record fields
                - source_bot_pk: ac_bots table primary key
                - source_bot_id: source bot_id
                - publish_bot_id: post-publish bot_id
                - name: bot name
                - owner_id: staff id
                - permission_owner: permission owner
                - ... other optional fields

        Returns:
            The created BotPublishRecord.
        """
        ...

    # ========================================================================
    # Query Operations
    # ========================================================================

    @abstractmethod
    def get_by_id(self, publish_id: int) -> Optional[BotPublishRecord]:
        """Get a publish record by id.

        Args:
            publish_id: record id

        Returns:
            BotPublishRecord, or None if not found.
        """
        ...

    @abstractmethod
    def get_by_publish_bot_id(
        self,
        publish_bot_id: str,
        owner_id: str,
        env: str,
        publish_status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        """Get the latest-version record by publish_bot_id and owner_id.

        Args:
            publish_bot_id: post-publish bot_id
            owner_id: staff id
            env: environment
            publish_status: optional publish-status filter

        Returns:
            BotPublishRecord, or None if not found.
        """
        ...

    @abstractmethod
    def get_draft_by_publish_bot_id(
        self,
        publish_bot_id: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        """Get the DRAFT row by publish_bot_id (not filtered by owner_id).

        publish_bot_id uniquely identifies a bot, so owner_id is not needed — this
        avoids a silent miss when the caller's owner_id differs from the record's
        creation owner_id (e.g. an org bot whose entity_id != staff id). Used only
        by record_draft_artifact.

        Args:
            publish_bot_id: post-publish bot_id (== source bot_id during draft)
            env: environment

        Returns:
            The DRAFT BotPublishRecord, or None if not found.
        """
        ...

    @abstractmethod
    def get_by_publish_bot_id_and_version(
        self,
        publish_bot_id: str,
        owner_id: str,
        version: int,
        env: str,
    ) -> Optional[BotPublishRecord]:
        """Get a record by publish_bot_id, owner_id and version.

        Args:
            publish_bot_id: post-publish bot_id
            owner_id: staff id
            version: version number
            env: environment

        Returns:
            BotPublishRecord, or None if not found.
        """
        ...

    @abstractmethod
    def list_by_owner(
        self,
        owner_id: str,
        env: str,
        status: Optional[str] = None,
    ) -> List[BotPublishRecord]:
        """List a user's publish records.

        Args:
            owner_id: staff id
            env: environment
            status: optional status filter

        Returns:
            List of BotPublishRecord.
        """
        ...

    @abstractmethod
    def list_by_source_bot(
        self,
        source_bot_pk: int,
        env: str,
    ) -> List[BotPublishRecord]:
        """List the publish records of a source bot.

        Args:
            source_bot_pk: ac_bots table primary key
            env: environment

        Returns:
            List of BotPublishRecord.
        """
        ...

    @abstractmethod
    def list_by_status(
        self,
        status: str,
        env: str,
    ) -> List[BotPublishRecord]:
        """List publish records by status.

        Args:
            status: publish status
            env: environment

        Returns:
            List of BotPublishRecord.
        """
        ...

    @abstractmethod
    def get_latest_by_source_bot_id_and_owner_and_status(
        self,
        source_bot_id: str,
        owner_id: str,
        status: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        """Get the latest publish record by source_bot_id + owner_id + status."""
        ...

    @abstractmethod
    def get_latest_success_by_source_bot_id(
        self,
        source_bot_id: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        """Get the latest status=success publish record by source_bot_id (owner-agnostic).

        Used to resolve a multi-instance entry bot_id → runtime binding_id: reads
        that record's ``ext.binding.online``. Deliberately NOT filtered by owner_id —
        an org bot's entity_id may differ from the creation owner_id (staff id), and
        an owner filter would silently miss it.

        Args:
            source_bot_id: source bot_id
            env: environment

        Returns:
            The latest success publish record, or None if not found.
        """
        ...

    @abstractmethod
    def get_by_last_pub_id(
        self,
        last_pub_id: int,
    ) -> Optional[BotPublishRecord]:
        """Get a publish record by its last-successful-publish id (idempotency support).

        Args:
            last_pub_id: the last successfully-published record id

        Returns:
            BotPublishRecord, or None if not found.

        Note:
            Used for the idempotent lookup in upgrade operations, ensuring a retry
            does not create a duplicate publish record.
        """
        ...

    # ========================================================================
    # Update Operations
    # ========================================================================

    @abstractmethod
    def update_status(
        self,
        publish_id: int,
        target_status: str,
        source_status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        """Update the publish status.

        Args:
            publish_id: record id
            target_status: target status
            source_status: optional source status; when given, the update succeeds
                only if the current status equals it (optimistic lock / CAS)

        Returns:
            The updated BotPublishRecord, or None if the source status did not match.
        """
        ...

    @abstractmethod
    def update_status_with_ext(
        self,
        publish_id: int,
        target_status: str,
        ext: Dict[str, Any],
        source_status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        """Update the publish status and ext field (optimistic lock).

        Args:
            publish_id: record id
            target_status: target status
            ext: ext (extension) field dict
            source_status: optional source status; when given, the update succeeds
                only if the current status equals it (optimistic lock / CAS)

        Returns:
            The updated BotPublishRecord, or None if the source status did not match.
        """
        ...

    @abstractmethod
    def compare_and_set_ext(
        self,
        *,
        publish_id: int,
        expected_ext: Optional[Dict[str, Any]],
        ext: Dict[str, Any],
    ) -> Optional[BotPublishRecord]:
        """Atomically replace ``ext`` only when its full stored value is unchanged.

        Unlike ``update_status_with_ext``, this CAS protects same-status writers
        (Restart/Scale/Caller) from overwriting one another.
        """
        ...

    @abstractmethod
    def compare_and_set_status_with_ext(
        self,
        *,
        publish_id: int,
        source_status: str,
        target_status: str,
        expected_ext: Optional[Dict[str, Any]],
        ext: Dict[str, Any],
    ) -> Optional[BotPublishRecord]:
        """Atomically replace status and ext when both snapshots still match."""
        ...

    @abstractmethod
    def rollback_flip(
        self,
        *,
        demoted_publish_id: int,
        demoted_ext: Dict[str, Any],
        demoted_from_status: str,
        demoted_to_status: str,
        restored_publish_id: int,
        restored_ext: Dict[str, Any],
        restored_from_status: str,
        restored_to_status: str,
    ) -> tuple[bool, bool]:
        """Atomically flip the two publish records of a rollback (one transaction).

        A rollback demotes the currently-live version and restores the previous one:

        - ``demoted``: ``from -> to`` (e.g. SUCCESS -> DRAFT), writing ``demoted_ext``;
        - ``restored``: ``from -> to`` (e.g. UPGRADED -> SUCCESS), writing ``restored_ext``.

        Both UPDATEs commit in the same transaction to avoid a "half-flip" (one row
        moved, the other not) that would leave ``can_rollback`` permanently
        refusing (#197). Each is an optimistic-lock CAS.

        Returns:
            ``(demoted_ok, restored_ok)``: whether each CAS matched.
        """
        ...

    @abstractmethod
    def update_version(
        self,
        publish_id: int,
        version: int,
        status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        """Update the version number (and optionally the status).

        Args:
            publish_id: record id
            version: new version number
            status: optional new status

        Returns:
            The updated BotPublishRecord.
        """
        ...

    @abstractmethod
    def update_last_pub_id(
        self,
        publish_id: int,
        last_pub_id: int,
    ) -> Optional[BotPublishRecord]:
        """Update the last-successful-publish id.

        Args:
            publish_id: record id
            last_pub_id: last successfully-published id

        Returns:
            The updated BotPublishRecord.
        """
        ...

    # ========================================================================
    # Delete Operations
    # ========================================================================

    @abstractmethod
    def delete(self, publish_id: int) -> bool:
        """Delete a publish record.

        Args:
            publish_id: record id

        Returns:
            Whether the delete succeeded.
        """
        ...


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
