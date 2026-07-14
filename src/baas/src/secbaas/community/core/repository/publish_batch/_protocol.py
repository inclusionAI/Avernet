"""Publish batch repository protocol."""

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ._record import PublishBatchRecord


@runtime_checkable
class PublishBatchRepository(Protocol):
    """Protocol for publish batch repository."""

    def insert_batch(
        self,
        *,
        tenant: str,
        env: str,
        domain: str,
        publish_id: int,
        bot_id: int,
        batch_index: int,
        batch_capacity: int,
        status: str,
        creator: str,
        modifier: str,
        gmt_start: datetime | None = None,
        gmt_complete: datetime | None = None,
        error_message: str | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> int: ...

    def get_by_id(
        self, batch_id: int, tenant: str, env: str
    ) -> PublishBatchRecord | None: ...

    def update_status(
        self,
        *,
        batch_id: int,
        tenant: str,
        env: str,
        status: str,
        modifier: str | None = None,
    ) -> None: ...

    def list_by_publish_id(
        self, publish_id: int, tenant: str, env: str
    ) -> list[PublishBatchRecord]: ...

    def list_by_publish_and_stage(
        self, publish_id: int, tenant: str, env: str, stage: str
    ) -> list[PublishBatchRecord]: ...

    def soft_delete(
        self, *, batch_id: int, tenant: str, env: str, modifier: str
    ) -> None: ...
