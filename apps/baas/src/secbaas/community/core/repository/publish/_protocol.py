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
        name: str | None = None,
        description: str | None = None,
        publisher: str | None = None,
        replica_desired: int | None = None,
        batch_capacity: int | None = None,
        batch_number: int | None = None,
        cooldown_seconds: int | None = None,
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

    def soft_delete(
        self, *, publish_id: int, tenant: str, env: str, modifier: str
    ) -> None: ...
