from typing import Any, Protocol, runtime_checkable

from ._record import BotRecord


@runtime_checkable
class BotRepository(Protocol):
    """Protocol for bot repository."""

    def insert_bot(
        self,
        *,
        bot_uuid: str,
        tenant: str,
        env: str,
        domain: str,
        creator: str,
        modifier: str,
        status: str,
        name: str,
        description: str | None,
        template_uuid: str | None,
        replica_desired: int,
        replica_minimum: int,
        replica_maximum: int,
        auto_scaling_enabled: int,
        sla_grade: str,
        extra_config: dict[str, Any] | None,
    ) -> int:
        """Insert a new bot record. Returns the new record ID."""
        ...

    def get_by_id(self, bot_id: int, tenant: str, env: str) -> BotRecord | None:
        """Get bot by primary key ID with tenant+env isolation."""
        ...

    def get_by_id_including_deleted(
        self, bot_id: int, tenant: str, env: str
    ) -> BotRecord | None:
        """Get bot by ID including soft-deleted records."""
        ...

    def get_by_bot_uuid(
        self, bot_uuid: str, tenant: str, env: str, status: str
    ) -> BotRecord | None:
        """Get bot by business UUID + status with tenant+env isolation.

        UK: (tenant, bot_uuid, status, is_deleted) — status is required for uniqueness.
        """
        ...

    def list_by_bot_uuid(self, bot_uuid: str, tenant: str, env: str) -> list[BotRecord]:
        """List all bot records by UUID (all statuses) with tenant+env isolation."""
        ...

    def get_active_by_bot_uuid(
        self, bot_uuid: str, tenant: str, env: str
    ) -> BotRecord | None:
        """Get ACTIVE bot by bot_uuid with tenant+env isolation.

        Returns the single ACTIVE bot record for the given bot_uuid.
        Raises RuntimeError if multiple ACTIVE bots found (data integrity violation).
        Returns None if no ACTIVE bot found.
        """
        ...

    def get_active_by_bot_uuid_only(self, bot_uuid: str) -> BotRecord | None:
        """Get ACTIVE bot by bot_uuid only (no tenant/env filter).

        Matches the 0525 SQL: baas_bot b ON b.bot_uuid = eb.device_id AND b.status = 'ACTIVE'
        Used by health-check validating/online queries.
        Does NOT filter is_deleted — matching the original SQL behaviour.
        """
        ...

    def update_bot(
        self,
        *,
        bot_id: int,
        tenant: str,
        env: str,
        name: str | None = None,
        description: str | None = None,
        modifier: str | None = None,
        extra_config: dict[str, Any] | None = None,
        replica_desired: int | None = None,
    ) -> int:
        """Update bot mutable fields with tenant+env isolation. Returns affected row count."""
        ...

    def update_status(
        self,
        *,
        bot_id: int,
        tenant: str,
        env: str,
        status: str,
        modifier: str,
    ) -> None:
        """Update bot status with tenant+env isolation. modifier is the user ID for audit."""
        ...

    def soft_delete(self, *, bot_id: int, tenant: str, env: str, modifier: str) -> None:
        """Soft delete bot with tenant+env isolation by setting is_deleted to the record ID (per D-04)."""
        ...

    def insert_bot_record(
        self,
        *,
        source_bot_id: int,
        tenant: str,
        env: str,
        status: str,
        extra_config: dict[str, Any] | None = None,
        name: str | None = None,
        modifier: str = "system",
    ) -> int:
        """Clone an existing bot record with a new status.

        Copies all fields from the source bot record, overriding status
        and optionally extra_config/name. Returns the new record ID.
        Used by UPDATE publish to create a PENDING bot record.
        """
        ...

    def list_bots(
        self,
        *,
        tenant: str,
        env: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[BotRecord]]:
        """List bots with tenant+env isolation and optional status filtering.

        Returns: (total_count, list_of_records)
        """
        ...

    def complete_destroy(
        self, *, bot_id: int, tenant: str, env: str, modifier: str
    ) -> None:
        """Atomically set bot to RELEASED and soft-delete.

        Both operations execute in a single transaction so partial failure
        cannot leave the bot in an inconsistent state.
        """
        ...

    def complete_stop(
        self,
        *,
        bot_id: int,
        tenant: str,
        env: str,
        modifier: str,
    ) -> None: ...

    def complete_update_transfer(
        self,
        *,
        old_bot_id: int,
        new_bot_id: int,
        device_uuids: list[str],
        domain: str,
        tenant: str,
        env: str,
        modifier: str,
    ) -> None:
        """Atomically transfer device relationships and bot statuses for UPDATE publish.

        Executes in a single transaction:
        1. Soft-delete old bot's device relationships
        2. Create new relationships linking devices to new bot
        3. Set new bot to ACTIVE
        4. Set old bot to RELEASED and soft-delete
        """
        ...
