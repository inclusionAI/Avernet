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


class TestPublishRecordRepositoryProtocol:
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

    def test_insert_and_get_by_id_roundtrip(
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
            result_status="PENDING",
            creator="test_user",
            modifier="test_user",
            trigger_source="manual",
            publish_reason="initial deployment",
            result_message="waiting for execution",
            extra_config={"region": "cn-hangzhou"},
        )
        assert record_id > 0

        record = publish_record_repository.get_by_id(record_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, PublishRecordRecord)
        assert record.id == record_id
        assert record.device_id == device_id
        assert record.bot_id == bot_id
        assert record.publish_id == publish_id
        assert record.batch_id == batch_id
        assert record.event_type == "CREATE"
        assert record.result_status == "PENDING"
        assert record.trigger_source == "manual"
        assert record.publish_reason == "initial deployment"
        assert record.result_message == "waiting for execution"
        assert record.extra_config == {"region": "cn-hangzhou"}

    def test_get_by_id_returns_none_for_missing(
        self,
        publish_record_repository: PublishRecordRepository,
        db_transaction,
    ):
        result = publish_record_repository.get_by_id(99999999, TEST_TENANT, TEST_ENV)
        assert result is None

    def test_list_by_batch_id(
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
            result_status="PENDING",
            creator="test_user",
            modifier="test_user",
        )
        # Second device in the same batch
        dev2 = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            provider_type=None,
        )
        r1 = publish_record_repository.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=dev2,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="SUCCESS",
            creator="test_user",
            modifier="test_user",
        )

        records = publish_record_repository.list_by_batch_id(
            batch_id, TEST_TENANT, TEST_ENV
        )
        assert len(records) == 2
        ids = {r.id for r in records}
        assert r0 in ids
        assert r1 in ids

    def test_list_by_batch_id_empty(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        publish_record_repository: PublishRecordRepository,
        device_repository: DeviceRepository,
        db_transaction,
    ):
        _, _, batch_id, _ = self._create_fk_chain(
            bot_repository,
            publish_repository,
            publish_batch_repository,
            device_repository,
        )
        records = publish_record_repository.list_by_batch_id(
            batch_id, TEST_TENANT, TEST_ENV
        )
        assert records == []

    def test_list_by_device_id(
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
            result_status="PENDING",
            creator="test_user",
            modifier="test_user",
        )
        r1 = publish_record_repository.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=device_id,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="UPDATE",
            result_status="SUCCESS",
            creator="test_user",
            modifier="test_user",
        )

        records = publish_record_repository.list_by_device_id(
            device_id, TEST_TENANT, TEST_ENV
        )
        assert len(records) == 2
        ids = {r.id for r in records}
        assert r0 in ids
        assert r1 in ids

    def test_list_by_device_id_empty(
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
        records = publish_record_repository.list_by_device_id(
            device_id, TEST_TENANT, TEST_ENV
        )
        assert records == []

    def test_update_result(
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
            result_status="PENDING",
            creator="test_user",
            modifier="test_user",
        )

        publish_record_repository.update_result(
            record_id=record_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            result_status="SUCCESS",
            result_message="deployment completed",
            modifier="admin",
        )

        record = publish_record_repository.get_by_id(record_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.result_status == "SUCCESS"
        assert record.result_message == "deployment completed"

    def test_update_result_if_processing_when_pending(
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

        updated = publish_record_repository.update_result_if_processing(
            record_id=record_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            result_status="SUCCESS",
            result_message="device started",
            modifier="admin",
        )
        assert updated is True

        record = publish_record_repository.get_by_id(record_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.result_status == "SUCCESS"

    def test_update_result_if_processing_when_already_done(
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
            result_status="SUCCESS",
            creator="test_user",
            modifier="test_user",
        )

        updated = publish_record_repository.update_result_if_processing(
            record_id=record_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            result_status="FAILED",
            modifier="admin",
        )
        assert updated is False

        record = publish_record_repository.get_by_id(record_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.result_status == "SUCCESS"

    def test_count_records_by_batch_id(
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
        dev2 = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            provider_type=None,
        )
        publish_record_repository.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=dev2,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="FAILED",
            creator="test_user",
            modifier="test_user",
        )

        counts = publish_record_repository.count_records_by_batch_id(
            batch_id, TEST_TENANT, TEST_ENV
        )
        assert isinstance(counts, dict)
        assert counts.get("SUCCESS", 0) == 1
        assert counts.get("FAILED", 0) == 1

    def test_count_records_by_batch_id_empty(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        publish_record_repository: PublishRecordRepository,
        device_repository: DeviceRepository,
        db_transaction,
    ):
        _, _, batch_id, _ = self._create_fk_chain(
            bot_repository,
            publish_repository,
            publish_batch_repository,
            device_repository,
        )
        counts = publish_record_repository.count_records_by_batch_id(
            batch_id, TEST_TENANT, TEST_ENV
        )
        assert counts == {}

    def test_get_by_device_id_and_publish_id(
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
            result_status="PENDING",
            creator="test_user",
            modifier="test_user",
        )

        record = publish_record_repository.get_by_device_id_and_publish_id(
            device_id, publish_id, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.id == record_id

    def test_get_by_device_id_and_publish_id_missing(
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
        result = publish_record_repository.get_by_device_id_and_publish_id(
            device_id, 99999999, TEST_TENANT, TEST_ENV
        )
        assert result is None

    def test_get_processing_record_by_device_and_publish(
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

        record = publish_record_repository.get_processing_record_by_device_and_publish(
            device_id, publish_id, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.id == record_id
        assert record.result_status == "PROCESSING"

    def test_get_processing_record_by_device_and_publish_not_processing(
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

        result = publish_record_repository.get_processing_record_by_device_and_publish(
            device_id, publish_id, TEST_TENANT, TEST_ENV
        )
        assert result is None

    def test_exists_record_for_device_and_publish_true(
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
            result_status="PENDING",
            creator="test_user",
            modifier="test_user",
        )

        exists = publish_record_repository.exists_record_for_device_and_publish(
            device_id, publish_id, TEST_TENANT, TEST_ENV
        )
        assert exists is True

    def test_exists_record_for_device_and_publish_false(
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
        exists = publish_record_repository.exists_record_for_device_and_publish(
            device_id, 99999999, TEST_TENANT, TEST_ENV
        )
        assert exists is False

    def test_update_device_id(
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
            result_status="PENDING",
            creator="test_user",
            modifier="test_user",
        )

        new_device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            provider_type=None,
        )

        publish_record_repository.update_device_id(
            record_id=record_id,
            device_id=new_device_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        record = publish_record_repository.get_by_id(record_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.device_id == new_device_id

    def test_count_records_by_publish_id(
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
        dev2 = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            provider_type=None,
        )
        publish_record_repository.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=dev2,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="FAILED",
            creator="test_user",
            modifier="test_user",
        )

        counts = publish_record_repository.count_records_by_publish_id(
            publish_id, TEST_TENANT, TEST_ENV
        )
        assert isinstance(counts, dict)
        assert counts.get("SUCCESS", 0) == 1
        assert counts.get("FAILED", 0) == 1
