"""BotPublish repository protocol — business-layer internal abstraction.

Defines the BotPublish data-access interface consumed by the service layer via
dependency injection.
"""
from typing import Dict, Any, List, Optional, Protocol

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord


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

    def get_by_id(self, publish_id: int) -> Optional[BotPublishRecord]:
        """Get a publish record by id.

        Args:
            publish_id: record id

        Returns:
            BotPublishRecord, or None if not found.
        """
        ...

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

    def get_latest_by_source_bot_id_and_owner_and_status(
        self,
        source_bot_id: str,
        owner_id: str,
        status: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        """Get the latest publish record by source_bot_id + owner_id + status."""
        ...

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

    def delete(self, publish_id: int) -> bool:
        """Delete a publish record.

        Args:
            publish_id: record id

        Returns:
            Whether the delete succeeded.
        """
        ...
