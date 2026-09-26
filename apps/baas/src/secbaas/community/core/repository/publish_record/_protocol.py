"""Publish record repository protocol."""

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ._record import PublishRecordExtraConfig, PublishRecordRecord


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

    def try_claim_retry(
        self,
        *,
        record_id: int,
        tenant: str,
        env: str,
        expected_retry_count: int,
        attempt_started_at: str,
        modifier: str | None = None,
    ) -> bool:
        """Atomically claim the next publish attempt for a record.

        Succeeds only when the record is still PROCESSING and its stored
        ``publish_retry_count`` still equals ``expected_retry_count``. On success the
        count is incremented and the attempt timestamp is persisted in
        extra_config. Returns True when this caller won the claim.
        """
        ...

    def get_retry_state(
        self, record_id: int, tenant: str, env: str
    ) -> PublishRecordExtraConfig | None:
        """Read the retry sub-state currently stored on a record."""
        ...

    def list_stale_processing_records_across_tenants(
        self, timeout_seconds: int, env: str
    ) -> list[PublishRecordRecord]:
        """List stale in-flight records for an environment across all tenants.

        The scheduled sweep has no request tenant, so it cannot filter by one.
        """
        ...

    def list_stale_processing_records_all(
        self, timeout_seconds: int, tenant: str, env: str
    ) -> list[PublishRecordRecord]:
        """List in-flight records past their attempt window across all publishes.

        Used by the server-side sweep so timeout-driven retries advance without
        a client polling for publish progress.
        """
        ...

    def database_now(self) -> datetime:
        """Current database clock.

        Timestamps written with ``func.now()`` share this clock, which may
        differ from the application host timezone.
        """
        ...

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
