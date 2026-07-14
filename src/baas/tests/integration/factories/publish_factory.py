"""Factory for publish, batch, and record entities."""

from typing import Any

from .base import DEFAULT_TEST_CREATOR, DEFAULT_TEST_DOMAIN, DEFAULT_TEST_MODIFIER


class PublishFactory:
    """Build publish/batch/record chains with minimal boilerplate."""

    def __init__(
        self,
        publish_repo: Any,
        batch_repo: Any,
        record_repo: Any,
        env: str,
    ) -> None:
        self.publish_repo = publish_repo
        self.batch_repo = batch_repo
        self.record_repo = record_repo
        self.env = env

    def create_publish(self, tenant: str, **overrides: Any) -> int:
        """Create a publish record and return its ID."""
        defaults: dict[str, Any] = {
            "tenant": tenant,
            "env": self.env,
            "domain": DEFAULT_TEST_DOMAIN,
            "bot_id": 1,
            "publish_type": "CREATE",
            "status": "PENDING",
            "creator": DEFAULT_TEST_CREATOR,
            "modifier": DEFAULT_TEST_MODIFIER,
        }
        defaults.update(overrides)
        return int(self.publish_repo.insert_publish(**defaults))

    def create_batch(self, tenant: str, publish_id: int, **overrides: Any) -> int:
        """Create a batch record and return its ID."""
        defaults: dict[str, Any] = {
            "tenant": tenant,
            "env": self.env,
            "domain": DEFAULT_TEST_DOMAIN,
            "publish_id": publish_id,
            "bot_id": 1,
            "batch_index": 0,
            "batch_capacity": 10,
            "status": "ACTIVE",
            "creator": DEFAULT_TEST_CREATOR,
            "modifier": DEFAULT_TEST_MODIFIER,
        }
        defaults.update(overrides)
        return int(self.batch_repo.insert_batch(**defaults))

    def create_record(
        self, tenant: str, publish_id: int, batch_id: int, **overrides: Any
    ) -> int:
        """Create a publish record and return its ID."""
        defaults: dict[str, Any] = {
            "tenant": tenant,
            "env": self.env,
            "domain": DEFAULT_TEST_DOMAIN,
            "device_id": 1,
            "bot_id": 1,
            "publish_id": publish_id,
            "batch_id": batch_id,
            "event_type": "CREATE",
            "result_status": "SUCCESS",
            "creator": DEFAULT_TEST_CREATOR,
            "modifier": DEFAULT_TEST_MODIFIER,
        }
        defaults.update(overrides)
        return int(self.record_repo.insert_record(**defaults))

    def create_publish_chain(
        self, tenant: str, num_records: int = 1, **overrides: Any
    ) -> tuple[int, int, list[int]]:
        """Create a publish, batch, and N records in one call.

        Args:
            tenant: Tenant name.
            num_records: Number of records to create under the batch.
            **overrides: Optional overrides keyed by entity type::
                - ``publish``: dict forwarded to ``create_publish``
                - ``batch``: dict forwarded to ``create_batch``
                - ``record``: dict forwarded to each ``create_record``

        Returns:
            ``(publish_id, batch_id, [record_id, ...])``
        """
        publish_ov: dict[str, Any] = overrides.pop("publish", {})
        batch_ov: dict[str, Any] = overrides.pop("batch", {})
        record_ov: dict[str, Any] = overrides.pop("record", {})

        publish_id = self.create_publish(tenant, **publish_ov)
        batch_id = self.create_batch(tenant, publish_id, **batch_ov)
        records = []
        for i in range(num_records):
            ro = dict(record_ov)
            ro.setdefault("device_id", i + 1)
            record_id = self.create_record(tenant, publish_id, batch_id, **ro)
            records.append(record_id)
        return publish_id, batch_id, records
