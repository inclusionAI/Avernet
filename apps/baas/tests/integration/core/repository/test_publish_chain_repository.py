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
            name="Publish-CREATE",
            publisher="test_user",
            replica_desired=1,
            batch_capacity=5,
            batch_number=1,
            cooldown_seconds=0,
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
