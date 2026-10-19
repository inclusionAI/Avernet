from typing import Protocol, runtime_checkable

from ._record import BotDeviceRelRecord


@runtime_checkable
class BotDeviceRelRepository(Protocol):
    """Protocol for bot-device relationship repository."""

    def insert_rel(
        self,
        *,
        bot_id: int,
        device_uuid: str,
        tenant: str,
        env: str,
        domain: str,
        creator: str,
        modifier: str,
    ) -> int:
        """Insert a new bot-device relationship. Returns the new record ID."""
        ...

    def get_by_id(
        self, rel_id: int, tenant: str, env: str
    ) -> BotDeviceRelRecord | None:
        """Get relationship by primary key ID with tenant+env isolation."""
        ...

    def list_by_bot_id(
        self, bot_id: int, tenant: str, env: str
    ) -> list[BotDeviceRelRecord]:
        """Get all device relationships for a given bot with tenant+env isolation."""
        ...

    def get_by_device_uuid(
        self, device_uuid: str, tenant: str, env: str
    ) -> BotDeviceRelRecord | None:
        """Get bot relationship for a given device with tenant+env isolation."""
        ...

    def soft_delete(self, *, rel_id: int, tenant: str, env: str, modifier: str) -> None:
        """Soft delete relationship with tenant+env isolation by setting is_deleted to the record ID (per D-04)."""
        ...

    def exists(self, *, bot_id: int, device_uuid: str, tenant: str, env: str) -> bool:
        """Check if a relationship exists between bot and device with tenant+env isolation."""
        ...

    def count_by_bot_id(self, bot_id: int, tenant: str, env: str) -> int:
        """Count total device relationships for a given bot with tenant+env isolation."""
        ...

    def soft_delete_by_bot_id(
        self, *, bot_id: int, tenant: str, env: str, modifier: str
    ) -> int:
        """Soft delete all relationships for a bot with tenant+env isolation. Returns affected count."""
        ...

    def max_live_bot_id_for_bot_ids(self, bot_ids: list[int]) -> int | None:
        """MAX(bot_id) over live rel rows whose bot_id is in ``bot_ids``.

        Live = ``is_deleted == 0`` per the D-04 tombstone convention (a
        soft-deleted row carries its own id in ``is_deleted``). No
        tenant/env filter — deliberate cross-env semantics mirroring the
        admin restore endpoint's contract (see ``get_by_id_only`` /
        ``restore_bot_record_by_id``). Returns None when the list is
        empty or no live rel row matches.
        """
        ...

    def batch_insert_rels(
        self,
        *,
        bot_id: int,
        device_uuids: list[str],
        tenant: str,
        env: str,
        domain: str,
        creator: str,
        modifier: str,
    ) -> list[int]:
        """Batch insert relationships for a bot with multiple device_uuids. Returns list of new IDs."""
        ...
