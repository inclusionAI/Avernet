from typing import Any, Protocol, runtime_checkable

from ._record import BotRecord, RestoredBotRecord


class BotRecordConflictError(Exception):
    """A live bot record already occupies the requested unique-key slot."""


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

    def get_by_id_only(self, bot_id: int) -> BotRecord | None:
        """Get bot by primary key ID only (no tenant/env filter).

        Filters is_deleted=0. Returns None if not found or soft-deleted.
        Used by admin endpoints that need cross-tenant/env lookup by PK.
        """
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

    def list_by_bot_uuid_including_deleted(
        self, bot_uuid: str, tenant: str, env: str
    ) -> list[BotRecord]:
        """List all bot records by UUID including soft-deleted ones.

        A successful DESTROY soft-deletes the bot record, which would otherwise
        hide its publishes (incl. the DESTROY workflow) from the publishes-by-bot
        listing — blinding client-side adopt-by-query for destroy operations.
        This variant keeps them visible; ``get_by_id_including_deleted`` exists
        for the same DESTROY reason.
        """
        ...

    def list_by_bot_uuid_only_including_deleted(self, bot_uuid: str) -> list[BotRecord]:
        """List ALL bot record ids sharing a bot_uuid (no tenant/env filter).

        The ``_only`` naming convention (see ``get_by_id_only`` /
        ``get_active_by_bot_uuid_only``) marks cross-tenant/env lookup —
        deliberate cross-env semantics for admin recovery, not a data-leak
        accident. Includes soft-deleted records as well as live ones (no
        ``is_deleted`` predicate). Used by the admin bot-restore endpoint's
        ``bot_uuid`` resolution, which deliberately performs no env validation
        anywhere in its chain.
        """
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
        Filters is_deleted=0 — returns live ACTIVE rows only (mirrors the ORM filter).
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

    def reconcile_unclaimed_pending_bots(
        self,
        bot_uuid: str,
        tenant: str,
        env: str,
        modifier: str = "system",
        exclude_bot_ids: set[int] | None = None,
    ) -> int:
        """Soft-delete every live PENDING bot row for the uuid in ONE transaction.

        Reconcile sweep for clones that no non-terminal publish claims
        (design D5): within a single session, list the live PENDING rows
        of ``(bot_uuid, tenant, env)`` ordered id descending, then for
        each row soft-delete its ``baas_bot_device_rel`` rows (rel
        tombstone: ``is_deleted = own rel id``, as in
        ``complete_update_transfer``) and the bot row itself
        (``soft_delete`` write shape: ``is_deleted = bot_id``,
        ``modifier``, ``gmt_modified``). Soft-deleted rows are never
        revived — each tombstone keeps its own unique-key slot — and
        non-PENDING rows are never touched. Returns the number of bot
        rows soft-deleted.

        ``exclude_bot_ids`` (design D5.6/D5.7 "claim-set protection") is
        the set of bot row ids that are NEVER selected for soft-deletion.
        It must contain (a) the resolved source bot row the new publish
        targets — a never-published first-generation PENDING row is the
        SOURCE of a CREATE publish, not an unclaimed clone, and sweeping
        it would destroy the bot itself — and (b) every ``target_bot_id``
        claimed by a non-finished publish across ALL generations of the
        uuid, including retired/soft-deleted ones: a uuid with any
        unfinished publish is in-flight, so its claimed clones must never
        be reconciled (the caller computes this via the include-deleted
        generation scan). ``None`` or an empty set means no exclusion —
        sweep every live PENDING row (callers unable to identify any
        protected id pass ``None`` deliberately).
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

    def restore_bot_record_by_id(
        self,
        bot_id: int,
        *,
        modifier: str,
    ) -> RestoredBotRecord | None:
        """Atomically restore a bot record to live ACTIVE by primary key.

        Deliberate, UK-ordered admin revival (``uk_tnt_bot_uuid_env_status_del``):
        within ONE transaction, (1) select the target by PK with NO
        is_deleted/tenant/env predicate, (2) retire every OTHER live row
        sharing its ``bot_uuid`` (``status=RELEASED``, ``is_deleted=<own
        row id>``), then (3) revive the target (``is_deleted=0``,
        ``status=ACTIVE``). Retiring siblings FIRST frees the live UK
        slot before the revive re-occupies it — the re-revival ordering
        warned about by ``try_insert_pending_bot`` (the historical MySQL
        1205 self-deadlock) must never be inverted. No SELECT ... FOR
        UPDATE is used (gap-lock deadlock class); the unique key is the
        final arbiter.

        Cross-env by requirement: sibling matching is on ``bot_uuid`` and
        ``id != bot_id`` only — no tenant/env filtering on the PK lookup
        or the sibling retirement.

        Returns None when no record exists for ``bot_id`` (live or
        soft-deleted) — the service layer maps that to its not-found
        error. Returns a :class:`RestoredBotRecord` on success. A unique
        key violation raises :class:`BotRecordConflictError` after the
        transaction has rolled back (no partial state).
        """
        ...
