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


# ======================================================================
# TestPublishBatchRepositoryProtocol — 6 methods
# ======================================================================


class TestPublishBatchRepositoryProtocol:
    """Integration tests for PublishBatchRepository Protocol against ZDAS MySQL.

    Every test uses ONLY the PublishBatchRepository Protocol.
    Requires bot_id → publish_id FK chain via bot_repository + publish_repository.
    """

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _create_bot_and_publish(
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
    ) -> tuple[int, int]:
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="PublishBatch Test Bot",
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
        )
        return bot_id, publish_id

    # 1. insert_batch + get_by_id (round-trip) --------------------------------

    def test_insert_and_get_by_id_roundtrip(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        db_transaction,
    ):
        bot_id, publish_id = self._create_bot_and_publish(
            bot_repository, publish_repository
        )
        gmt_start = datetime(2025, 5, 25, 10, 0, 0, tzinfo=UTC)
        gmt_complete = datetime(2025, 5, 25, 10, 5, 0, tzinfo=UTC)

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
            gmt_start=gmt_start,
            gmt_complete=gmt_complete,
            error_message="test error",
            extra_config={"stage": "PREPUB", "cooldown_seconds": 60},
        )
        assert batch_id > 0

        record = publish_batch_repository.get_by_id(batch_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, PublishBatchRecord)
        assert record.id == batch_id
        assert record.publish_id == publish_id
        assert record.bot_id == bot_id
        assert record.batch_index == 0
        assert record.batch_capacity == 10
        assert record.status == "PENDING"
        assert record.creator == "test_user"
        assert record.error_message == "test error"
        assert record.stage == "PREPUB"
        assert record.cooldown_seconds == 60

    # 2. get_by_id returns None for missing -----------------------------------

    def test_get_by_id_returns_none_for_missing(
        self,
        publish_batch_repository: PublishBatchRepository,
        db_transaction,
    ):
        result = publish_batch_repository.get_by_id(99999999, TEST_TENANT, TEST_ENV)
        assert result is None

    # 3. update_status ---------------------------------------------------------

    def test_update_status(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        db_transaction,
    ):
        bot_id, publish_id = self._create_bot_and_publish(
            bot_repository, publish_repository
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
            extra_config={"stage": "PREPUB"},
        )

        publish_batch_repository.update_status(
            batch_id=batch_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="admin",
        )

        record = publish_batch_repository.get_by_id(batch_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "ACTIVE"

    # 4. list_by_publish_id ----------------------------------------------------

    def test_list_by_publish_id(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        db_transaction,
    ):
        bot_id, publish_id = self._create_bot_and_publish(
            bot_repository, publish_repository
        )
        id0 = publish_batch_repository.insert_batch(
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
        )
        id1 = publish_batch_repository.insert_batch(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            publish_id=publish_id,
            bot_id=bot_id,
            batch_index=1,
            batch_capacity=20,
            status="ACTIVE",
            creator="test_user",
            modifier="test_user",
        )

        records = publish_batch_repository.list_by_publish_id(
            publish_id, TEST_TENANT, TEST_ENV
        )
        assert len(records) == 2
        ids = {r.id for r in records}
        assert id0 in ids
        assert id1 in ids

    def test_list_by_publish_id_empty(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        db_transaction,
    ):
        bot_id, publish_id = self._create_bot_and_publish(
            bot_repository, publish_repository
        )
        records = publish_batch_repository.list_by_publish_id(
            publish_id, TEST_TENANT, TEST_ENV
        )
        assert records == []

    # 5. list_by_publish_and_stage ---------------------------------------------

    def test_list_by_publish_and_stage(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        db_transaction,
    ):
        bot_id, publish_id = self._create_bot_and_publish(
            bot_repository, publish_repository
        )
        id0 = publish_batch_repository.insert_batch(
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
        id1 = publish_batch_repository.insert_batch(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            publish_id=publish_id,
            bot_id=bot_id,
            batch_index=1,
            batch_capacity=20,
            status="PENDING",
            creator="test_user",
            modifier="test_user",
            extra_config={"stage": "GRAY", "cooldown_seconds": 30},
        )

        prepub = publish_batch_repository.list_by_publish_and_stage(
            publish_id, TEST_TENANT, TEST_ENV, "PREPUB"
        )
        assert len(prepub) == 1
        assert prepub[0].id == id0

        gray = publish_batch_repository.list_by_publish_and_stage(
            publish_id, TEST_TENANT, TEST_ENV, "GRAY"
        )
        assert len(gray) == 1
        assert gray[0].id == id1

        unknown = publish_batch_repository.list_by_publish_and_stage(
            publish_id, TEST_TENANT, TEST_ENV, "PROD_FIRST_BATCH"
        )
        assert unknown == []

    # 6. soft_delete -----------------------------------------------------------

    def test_soft_delete(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        publish_batch_repository: PublishBatchRepository,
        db_transaction,
    ):
        bot_id, publish_id = self._create_bot_and_publish(
            bot_repository, publish_repository
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
        )

        publish_batch_repository.soft_delete(
            batch_id=batch_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        result = publish_batch_repository.get_by_id(batch_id, TEST_TENANT, TEST_ENV)
        assert result is None


# ======================================================================
# TestPublishRecordRepositoryProtocol — 14 methods
# ======================================================================


class TestPublishRecordRepositoryProtocol:
    """Integration tests for PublishRecordRepository Protocol against ZDAS MySQL.

    Every test uses ONLY the PublishRecordRepository Protocol.
    Requires bot_id → publish_id → batch_id + device_id FK chain.
    """

    # -- helpers --------------------------------------------------------------

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

    # 1. insert_record + get_by_id (round-trip) --------------------------------

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

    # 2. list_by_batch_id ------------------------------------------------------

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

    # 3. list_by_device_id -----------------------------------------------------

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

    # 4. update_result ---------------------------------------------------------

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

    # 5. update_result_if_processing -------------------------------------------

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

    # 6. count_records_by_batch_id ---------------------------------------------

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

    # 7. get_by_device_id_and_publish_id ---------------------------------------

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

    # 8. get_processing_record_by_device_and_publish ---------------------------

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

    # 9. exists_record_for_device_and_publish ----------------------------------

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

    # 10. update_device_id -----------------------------------------------------

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

    # 11. count_records_by_publish_id ------------------------------------------

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

    # 12. list_stale_processing_records ----------------------------------------

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

    # 13. get_latest_processing_record_by_device -------------------------------

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
