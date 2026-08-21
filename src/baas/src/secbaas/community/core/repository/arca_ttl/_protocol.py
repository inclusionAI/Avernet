from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class TtlRenewalScheduleRepository(Protocol):
    """Protocol for the ARCA TTL renewal schedule repository.

    Method set mirrors the operations the renewal engine requires against
    the baas_arca_ttl_renewal_schedule table (design doc §7.4). Every
    method takes an ``env`` parameter — pre/prod share one MySQL instance,
    so all statements must be env-scoped.
    """

    def register(
        self,
        env: str,
        sandbox_id: str,
        source_table: str,
        source_id: int,
        next_renew_at: datetime,
    ) -> None:
        """Register (or re-register) a container in the schedule table.

        Atomic upsert keyed on uk_source: a STOPPED row is resurrected to
        ACTIVE with a fresh schedule and a zeroed failure count.
        """
        ...

    def register_if_missing(
        self,
        env: str,
        sandbox_id: str,
        source_table: str,
        source_id: int,
        next_renew_at: datetime,
    ) -> None:
        """Register a container only if it is not already scheduled.

        Used by the discovery scan / gap detection path; idempotent via
        the uk_source unique index.
        """
        ...

    def list_due_for_renewal(
        self,
        env: str,
        source_table: str,
        limit: int = 500,
        *,
        now: datetime,
    ) -> list[dict]:
        """Query ACTIVE rows where next_renew_at < :now (caller-supplied).

        ``now`` is a naive-UTC datetime computed by the caller (CR-01
        clock domain) so the due gate is time-zone independent of the
        DB server clock. LEFT JOINs the corresponding hot table to
        verify the container still exists. Returns dict rows with
        hot_id for orphan detection.
        """
        ...

    def update_after_success(
        self,
        env: str,
        source_table: str,
        source_id: int,
        next_renew_at: datetime,
    ) -> None:
        """Update the schedule after a successful TTL renewal."""
        ...

    def update_after_failure(
        self,
        env: str,
        source_table: str,
        source_id: int,
        next_renew_at: datetime,
        new_fail_count: int,
    ) -> None:
        """Update the schedule after a TTL renewal failure."""
        ...

    def postpone_renewal(
        self,
        env: str,
        source_table: str,
        source_id: int,
        next_renew_at: datetime,
    ) -> None:
        """Reschedule renewal without recording a renewal event."""
        ...

    def count_active(self, env: str) -> int:
        """Count ACTIVE rows in the schedule table for the given env."""
        ...

    def find_unregistered(
        self,
        env: str,
        side: str,
        limit: int = 500,
    ) -> list[dict]:
        """Discover hot-table ARCA containers not yet in the schedule (anti-join)."""
        ...

    def set_status(
        self,
        env: str,
        source_table: str,
        source_id: int,
        status: str,
    ) -> None:
        """Update the status of a schedule record."""
        ...

    def count_hot_arca_devices(self, env: str) -> int:
        """Count ACTIVE ARCA devices in the baas_device hot table for env."""
        ...

    def count_hot_arca_bindings(self, env: str) -> int:
        """Count ACTIVE ARCA bindings in ac_entity_device_binding for env."""
        ...
