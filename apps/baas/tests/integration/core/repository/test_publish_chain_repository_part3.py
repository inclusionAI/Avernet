"""Integration tests for PublishBatchRepository and PublishRecordRepository
Protocols against real ZDAS MySQL.

Every test uses ONLY Protocol types — no Zdas* references allowed.
db_transaction ensures all changes are rolled back.

FK chain: bot → publish → batch → record, plus device.
"""

import time
from datetime import UTC, datetime, timezone
from uuid import uuid4

import pytest

from secbaas.community.core.repository.bot import BotRepository
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.repository.publish import PublishRepository
from secbaas.community.core.repository.publish_batch import (
    PublishBatchRecord,
    PublishBatchRepository,
)
from secbaas.community.core.repository.publish_record import (
    PublishRecordRecord,
    PublishRecordRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration


TEST_ENV = get_current_env()


TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


class TestPublishRecordRepositoryProtocolPart2:
    """Integration tests for PublishRecordRepository Protocol against ZDAS MySQL.

    Every test uses ONLY the PublishRecordRepository Protocol.
    Requires bot_id → publish_id → batch_id + device_id FK chain.
    """

    @staticmethod
    def _create_fk_chain(
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        device_repository: DeviceRepository,
    ) -> tuple[int, int, int, int]:
        """Create full FK chain: bot → publish → batch, plus device. Returns (bot_id, publish_id, batch_id, device_id)."""
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="PublishRecord Test Bot",
        )
        publish_id = publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
            name="Publish-CREATE",
            publisher="test_user",
            replica_desired=1,
            batch_capacity=5,
            batch_number=1,
            cooldown_seconds=0,
        )
        batch_id = publish_batch_repository.insert_batch(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            publish_id=publish_id,
            bot_id=bot_id,
            batch_index=0,
            batch_capacity=10,
            status="PENDING",
            creator="test_user",
            modifier="test_user",
            extra_config={"stage": "PREPUB", "cooldown_seconds": 0},
        )
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            provider_type=None,
        )
        return bot_id, publish_id, batch_id, device_id

    def test_count_records_by_publish_id_empty(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        publish_record_repository: PublishRecordRepository,
        device_repository: DeviceRepository,
        db_transaction,
    ):
        _, publish_id, _, _ = self._create_fk_chain(
            bot_repository,
            publish_repository,
            publish_batch_repository,
            device_repository,
        )
        counts = publish_record_repository.count_records_by_publish_id(
            publish_id, TEST_TENANT, TEST_ENV
        )
        assert counts == {}

    def test_list_stale_processing_records(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        publish_record_repository: PublishRecordRepository,
        device_repository: DeviceRepository,
        db_transaction,
    ):
        bot_id, publish_id, batch_id, device_id = self._create_fk_chain(
            bot_repository,
            publish_repository,
            publish_batch_repository,
            device_repository,
        )
        record_id = publish_record_repository.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=device_id,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="PROCESSING",
            creator="test_user",
            modifier="test_user",
        )

        # Stale check with very large timeout — record should NOT be stale
        stale = publish_record_repository.list_stale_processing_records(
            publish_id, 86400, TEST_TENANT, TEST_ENV
        )
        ids = [r.id for r in stale]
        assert record_id not in ids

        # Sleep 1s to ensure gmt_create < NOW() for timeout=0 stale check
        time.sleep(1)

        # Stale check with 0 second timeout — record SHOULD be stale
        stale = publish_record_repository.list_stale_processing_records(
            publish_id, 0, TEST_TENANT, TEST_ENV
        )
        ids = [r.id for r in stale]
        assert record_id in ids

    def test_list_stale_processing_records_ignores_non_processing(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        publish_record_repository: PublishRecordRepository,
        device_repository: DeviceRepository,
        db_transaction,
    ):
        bot_id, publish_id, batch_id, device_id = self._create_fk_chain(
            bot_repository,
            publish_repository,
            publish_batch_repository,
            device_repository,
        )
        publish_record_repository.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=device_id,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="SUCCESS",
            creator="test_user",
            modifier="test_user",
        )

        stale = publish_record_repository.list_stale_processing_records(
            publish_id, 0, TEST_TENANT, TEST_ENV
        )
        assert stale == []

    def test_get_latest_processing_record_by_device(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        publish_record_repository: PublishRecordRepository,
        device_repository: DeviceRepository,
        db_transaction,
    ):
        bot_id, publish_id, batch_id, device_id = self._create_fk_chain(
            bot_repository,
            publish_repository,
            publish_batch_repository,
            device_repository,
        )
        r0 = publish_record_repository.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=device_id,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="PROCESSING",
            creator="test_user",
            modifier="test_user",
        )
        time.sleep(0.1)
        r1 = publish_record_repository.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=device_id,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="PROCESSING",
            creator="test_user",
            modifier="test_user",
        )

        record = publish_record_repository.get_latest_processing_record_by_device(
            device_id, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.id == r1

    def test_get_latest_processing_record_by_device_only_processing(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        publish_record_repository: PublishRecordRepository,
        device_repository: DeviceRepository,
        db_transaction,
    ):
        bot_id, publish_id, batch_id, device_id = self._create_fk_chain(
            bot_repository,
            publish_repository,
            publish_batch_repository,
            device_repository,
        )
        publish_record_repository.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=device_id,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="SUCCESS",
            creator="test_user",
            modifier="test_user",
        )
        time.sleep(0.1)
        r1 = publish_record_repository.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=device_id,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="PROCESSING",
            creator="test_user",
            modifier="test_user",
        )

        record = publish_record_repository.get_latest_processing_record_by_device(
            device_id, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.id == r1

    def test_get_latest_processing_record_by_device_none(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        publish_record_repository: PublishRecordRepository,
        device_repository: DeviceRepository,
        db_transaction,
    ):
        _, _, _, device_id = self._create_fk_chain(
            bot_repository,
            publish_repository,
            publish_batch_repository,
            device_repository,
        )
        result = publish_record_repository.get_latest_processing_record_by_device(
            device_id, TEST_TENANT, TEST_ENV
        )
        assert result is None
