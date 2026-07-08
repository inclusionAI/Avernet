"""
Device repository for baas_device table.

Implements ZDAS sync SQL pattern with soft-delete support per D-04.
Includes list_by_bot_id with JOIN to baas_bot_device_rel table.
"""

from typing import Any, Protocol, runtime_checkable

from ._record import DeviceRecord


@runtime_checkable
class DeviceRepository(Protocol):
    """Protocol for device repository."""

    def insert_device(
        self,
        *,
        device_uuid: str,
        tenant: str,
        env: str,
        domain: str,
        creator: str,
        modifier: str,
        status: str,
        provider_type: str | None,
        provider_device_id: str | None,
        provider_device_props: dict[str, Any] | None,
        extra_config: dict[str, Any] | None,
    ) -> int:
        """Insert a new device record. Returns the new record ID."""
        ...

    def get_by_id(self, device_id: int, tenant: str, env: str) -> DeviceRecord | None:
        """Get device by primary key ID with tenant+env isolation."""
        ...

    def get_by_ids(
        self, device_ids: list[int], tenant: str, env: str
    ) -> dict[int, DeviceRecord]:
        """Get multiple devices by primary key IDs. Returns dict keyed by id."""
        ...

    def get_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str, status: str
    ) -> DeviceRecord | None:
        """Get device by business UUID + status with tenant+env isolation.

        UK: (tenant, device_uuid, status, is_deleted) — status is required for uniqueness.
        """
        ...

    def get_by_device_uuid_only(self, device_uuid: str) -> DeviceRecord | None:
        """Get device by device_uuid only (global unique key lookup).

        device_uuid is a physical unique key for baas_device table.
        This method queries across all tenants and envs by device_uuid alone.
        """
        ...

    def list_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str
    ) -> list[DeviceRecord]:
        """List all device records by UUID (all statuses) with tenant+env isolation."""
        ...

    def list_active_local_devices_by_machine_user(
        self, machine_id: str, user_id: str, env: str
    ) -> list[DeviceRecord]:
        """List ACTIVE local devices by machine_id and user_id using provider_device_id LIKE pattern.

        Uses the pattern f"%--{machine_id}--{user_id}@%" to match provider_device_id
        which has the format: container_id--machine_id--user_id@template_id
        """
        ...

    def get_active_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str
    ) -> DeviceRecord | None:
        """Get ACTIVE device by UUID with tenant+env isolation."""
        ...

    def get_active_or_updating_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str
    ) -> DeviceRecord | None:
        """Get ACTIVE or UPDATING device by UUID with tenant+env isolation.

        Used by destroy_device_by_uuid() which is called after restart_device()
        and _execute_update_batch() set device to UPDATING status.
        """
        ...

    def get_by_provider_device_id_like(
        self, provider_device_id_prefix: str
    ) -> DeviceRecord | None:
        """Get device by provider_device_id prefix (any status except deleted).

        Used for reverse lookup by sandbox_id.
        """
        ...

    def get_by_provider_device_id_prefix(
        self, prefix: str, env: str
    ) -> DeviceRecord | None:
        """Get device by provider_device_id prefix with env filtering.

        Uses right-prefix query: provider_device_id LIKE '{prefix}%'
        Returns most recent match (ORDER BY id DESC LIMIT 1).
        """
        ...

    def update_device(
        self,
        *,
        device_id: int,
        tenant: str,
        env: str,
        modifier: str | None = None,
        provider_type: str | None = None,
        provider_device_id: str | None = None,
        provider_device_props: dict[str, Any] | None = None,
        extra_config: dict[str, Any] | None = None,
        status: str | None = None,
        err_msg: str | None = None,
    ) -> int:
        """Update device fields with tenant+env isolation. Returns affected row count."""
        ...

    def update_status(
        self, *, device_id: int, tenant: str, env: str, status: str
    ) -> None:
        """Update device status with tenant+env isolation."""
        ...

    def soft_delete(
        self, *, device_id: int, tenant: str, env: str, modifier: str
    ) -> None:
        """Soft delete device with tenant+env isolation by setting is_deleted to the record ID (per D-04)."""
        ...

    def soft_delete_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str, modifier: str
    ) -> int:
        """Soft delete device by device_uuid with tenant+env isolation. Returns affected row count."""
        ...

    def update_status_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str, status: str
    ) -> int:
        """Update device status by device_uuid with tenant+env isolation. Returns affected row count."""
        ...

    def batch_update_status_to_offline(self, device_ids: list[int], env: str) -> int:
        """Batch update device status to OFFLINE for given device IDs.

        Args:
            device_ids: List of device primary key IDs to update
            env: Environment for isolation

        Returns:
            Number of affected rows (0 if device_ids is empty or no matches)
        """
        ...

    def list_devices(
        self,
        *,
        tenant: str,
        env: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[DeviceRecord]]:
        """List devices with tenant+env isolation and optional status filtering."""
        ...

    def list_by_bot_id(
        self, *, bot_id: int, tenant: str, env: str
    ) -> list[DeviceRecord]:
        """Get devices associated with a bot via baas_bot_device_rel table with tenant+env isolation."""
        ...

    def list_active_devices_by_bot_id(self, *, bot_id: int) -> list[DeviceRecord]:
        """Get ACTIVE devices associated with a bot via baas_bot_device_rel (no tenant/env filter).

        Matches the 0525 SQL:
            INNER JOIN baas_bot_device_rel r ON r.bot_id = b.id AND r.is_deleted = 0
            INNER JOIN baas_device d ON d.device_uuid = r.device_uuid
                AND d.is_deleted = 0 AND d.status = 'ACTIVE'
        Used by health-check validating/online queries.
        """
        ...

    def list_devices_by_bot_ids(
        self, *, bot_ids: list[int], tenant: str, env: str
    ) -> dict[int, list[DeviceRecord]]:
        """Get devices for multiple bots in a single query. Returns dict keyed by bot_id."""
        ...
