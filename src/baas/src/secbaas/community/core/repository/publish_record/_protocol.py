"""Publish record repository protocol."""

from typing import Any, Protocol, runtime_checkable

from ._record import PublishRecordRecord


@runtime_checkable
class PublishRecordRepository(Protocol):
    """Protocol for publish record repository."""

    def insert_record(
        self,
        *,
        tenant: str,
        env: str,
        domain: str,
        device_id: int | None,
        bot_id: int | None,
        publish_id: int | None,
        batch_id: int | None,
        event_type: str,
        result_status: str,
        creator: str,
        modifier: str,
        trigger_source: str | None = None,
        publish_reason: str | None = None,
        result_message: str | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> int: ...

    def get_by_id(
        self, record_id: int, tenant: str, env: str
    ) -> PublishRecordRecord | None: ...

    def list_by_batch_id(
        self, batch_id: int, tenant: str, env: str
    ) -> list[PublishRecordRecord]: ...

    def list_by_device_id(
        self, device_id: int, tenant: str, env: str
    ) -> list[PublishRecordRecord]: ...

    def update_result(
        self,
        *,
        record_id: int,
        tenant: str,
        env: str,
        result_status: str,
        result_message: str | None = None,
        modifier: str | None = None,
    ) -> None: ...

    def update_result_if_processing(
        self,
        *,
        record_id: int,
        tenant: str,
        env: str,
        result_status: str,
        result_message: str | None = None,
        modifier: str | None = None,
    ) -> bool: ...

    def list_by_publish_id_and_batch_id(
        self,
        publish_id: int,
        batch_id: int,
        tenant: str,
        env: str,
        status: str | None = None,
    ) -> list[PublishRecordRecord]: ...

    def get_by_device_id_and_publish_id(
        self, device_id: int, publish_id: int, tenant: str, env: str
    ) -> PublishRecordRecord | None: ...

    def get_processing_record_by_device_and_publish(
        self, device_id: int, publish_id: int, tenant: str, env: str
    ) -> PublishRecordRecord | None: ...

    def exists_record_for_device_and_publish(
        self, device_id: int, publish_id: int, tenant: str, env: str
    ) -> bool: ...

    def update_device_id(
        self,
        *,
        record_id: int,
        device_id: int,
        tenant: str,
        env: str,
        modifier: str | None = None,
    ) -> None: ...

    def count_records_by_publish_id(
        self, publish_id: int, tenant: str, env: str
    ) -> dict[str, int]: ...

    def list_stale_processing_records(
        self, publish_id: int, timeout_seconds: int, tenant: str, env: str
    ) -> list[PublishRecordRecord]: ...

    def get_latest_processing_record_by_device(
        self, device_id: int, tenant: str, env: str
    ) -> PublishRecordRecord | None:
        """Get latest PROCESSING publish record for device.

        Filters: device_id, tenant, env, result_status='PROCESSING'
        Orders: id DESC (most recent first)
        """
        ...

    def count_records_by_batch_id(
        self, batch_id: int, tenant: str, env: str
    ) -> dict[str, int]:
        """Count publish records grouped by result_status for a batch.

        Filters: batch_id, tenant, env
        Returns: dict mapping status string to count
        """
        ...
