"""Publish repository protocol."""

from typing import Any, Protocol, runtime_checkable

from ._record import PublishRecord


@runtime_checkable
class PublishRepository(Protocol):
    """Protocol for publish repository."""

    def insert_publish(
        self,
        *,
        tenant: str,
        env: str,
        domain: str,
        bot_id: int,
        publish_type: str,
        status: str,
        creator: str,
        modifier: str,
        name: str,
        description: str | None = None,
        publisher: str,
        replica_desired: int,
        batch_capacity: int,
        batch_number: int,
        cooldown_seconds: int,
        config_version: str | None = None,
        last_publish_id: int | None = None,
        changelog: str | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> int: ...

    def get_by_id(
        self, publish_id: int, tenant: str, env: str
    ) -> PublishRecord | None: ...

    def update_status(
        self,
        *,
        publish_id: int,
        tenant: str,
        env: str,
        status: str,
        modifier: str | None = None,
    ) -> None: ...

    def update_publish(
        self,
        *,
        publish_id: int,
        tenant: str,
        env: str,
        extra_config: dict[str, Any] | None = None,
        modifier: str | None = None,
    ) -> int: ...

    def list_by_bot_id(
        self, bot_id: int, tenant: str, env: str
    ) -> list[PublishRecord]: ...

    def get_active_by_bot_id(
        self, bot_id: int, tenant: str, env: str
    ) -> PublishRecord | None: ...

    def list_active_by_bot_ids(
        self, bot_ids: list[int], tenant: str, env: str
    ) -> list[PublishRecord]:
        """List ALL non-terminal publishes whose bot_id is in ``bot_ids``.

        Same contract as ``get_active_by_bot_id`` extended across a
        generation id list: filters ``tenant``, ``env``, ``is_deleted=0``
        and the exact non-terminal status literal (NOT IN
        SUCCESS/FAILED/REJECTED/REVOKED, kept in lockstep with
        ``get_active_by_bot_id``), ordered id descending. An empty
        ``bot_ids`` returns an empty list without querying.
        """
        ...

    def soft_delete(
        self, *, publish_id: int, tenant: str, env: str, modifier: str
    ) -> None: ...
