from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.repository.publish import PublishRepository
from secbaas.core.repository.publish_batch import (
    PublishBatchRecord,
    PublishBatchRepository,
)
from secbaas.core.repository.publish_record import (
    PublishRecordRecord,
    PublishRecordRepository,
)
from secbaas.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


def _create_bot() -> int:
    repo = get_container().repository.bot_repository()
    return repo.insert_bot(
        bot_uuid=_generate_uuid(),
        tenant=TEST_TENANT,
        env=TEST_ENV,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
        name="Publish Chain Bot",
    )


def _create_publish(bot_id: int) -> int:
    repo = get_container().repository.publish_repository()
    return repo.insert_publish(
        tenant=TEST_TENANT,
        env=TEST_ENV,
        domain="test_domain",
        bot_id=bot_id,
        publish_type="CREATE",
        status="PENDING",
        creator="test_user",
        modifier="test_user",
    )


def _create_device() -> int:
    repo = get_container().repository.device_repository()
    return repo.insert_device(
        device_uuid=_generate_uuid(),
        tenant=TEST_TENANT,
        env=TEST_ENV,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
        status="PENDING",
        provider_type="ARCA",
        provider_device_id="sandbox-equiv",
        provider_device_props={"region": "test"},
        extra_config={},
    )


class TestPublishBatchSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: PublishBatchRepository = (
            get_container().repository.publish_batch_repository()
        )
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)

        batch_id = repo.insert_batch(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            publish_id=publish_id,
            bot_id=bot_id,
            batch_index=0,
            batch_capacity=10,
            status="PENDING",
            creator="u",
            modifier="u",
            extra_config={"stage": "PREPUB"},
        )
        assert batch_id > 0

        record = repo.get_by_id(batch_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, PublishBatchRecord)
        assert record.publish_id == publish_id
        assert record.bot_id == bot_id
        assert record.batch_capacity == 10
        assert record.status == "PENDING"
        assert record.is_deleted == 0
        assert record.gmt_create is not None

    def test_deep_update_status(self):
        repo = get_container().repository.publish_batch_repository()
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)
        batch_id = repo.insert_batch(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            publish_id=publish_id,
            bot_id=bot_id,
            batch_index=0,
            batch_capacity=10,
            status="PENDING",
            creator="u",
            modifier="u",
            extra_config={"stage": "PREPUB"},
        )
        repo.update_status(
            batch_id=batch_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="admin",
        )
        record = repo.get_by_id(batch_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "ACTIVE"

    def test_insert_batch_and_get_by_id(self):
        repo: PublishBatchRepository = (
            get_container().repository.publish_batch_repository()
        )
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)

        batch_id = repo.insert_batch(
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
        assert batch_id > 0

        record = repo.get_by_id(batch_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, PublishBatchRecord)
        assert record.id == batch_id
        assert record.publish_id == publish_id
        assert record.bot_id == bot_id
        assert record.batch_index == 0
        assert record.batch_capacity == 10
        assert record.status == "PENDING"
        assert record.tenant == TEST_TENANT
        assert record.env == TEST_ENV
        assert record.is_deleted == 0
        assert record.gmt_create is not None

    def test_update_status(self):
        repo = get_container().repository.publish_batch_repository()
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)

        batch_id = repo.insert_batch(
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
        repo.update_status(
            batch_id=batch_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="admin",
        )
        record = repo.get_by_id(batch_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "ACTIVE"

    def test_list_by_publish_id(self):
        repo = get_container().repository.publish_batch_repository()
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)

        id0 = repo.insert_batch(
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
        id1 = repo.insert_batch(
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
        )

        records = repo.list_by_publish_id(publish_id, TEST_TENANT, TEST_ENV)
        assert len(records) == 2
        ids = {r.id for r in records}
        assert id0 in ids
        assert id1 in ids

    def test_soft_delete(self):
        repo = get_container().repository.publish_batch_repository()
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)

        batch_id = repo.insert_batch(
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
        repo.soft_delete(
            batch_id=batch_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )
        result = repo.get_by_id(batch_id, TEST_TENANT, TEST_ENV)
        assert result is None


class TestPublishRecordSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: PublishRecordRepository = (
            get_container().repository.publish_record_repository()
        )
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)
        device_id = _create_device()
        batch_repo = get_container().repository.publish_batch_repository()
        batch_id = batch_repo.insert_batch(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            publish_id=publish_id,
            bot_id=bot_id,
            batch_index=0,
            batch_capacity=10,
            status="PENDING",
            creator="u",
            modifier="u",
            extra_config={"stage": "PREPUB"},
        )

        record_id = repo.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=device_id,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="PENDING",
            creator="u",
            modifier="u",
            trigger_source=None,
            publish_reason=None,
            result_message=None,
            extra_config={},
        )
        assert record_id > 0

        record = repo.get_by_id(record_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, PublishRecordRecord)
        assert record.device_id == device_id
        assert record.bot_id == bot_id
        assert record.publish_id == publish_id
        assert record.batch_id == batch_id
        assert record.event_type == "CREATE"
        assert record.result_status == "PENDING"
        assert record.gmt_create is not None

    def test_deep_update_result(self):
        repo = get_container().repository.publish_record_repository()
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)
        device_id = _create_device()
        batch_repo = get_container().repository.publish_batch_repository()
        batch_id = batch_repo.insert_batch(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            publish_id=publish_id,
            bot_id=bot_id,
            batch_index=0,
            batch_capacity=10,
            status="PENDING",
            creator="u",
            modifier="u",
            extra_config={"stage": "PREPUB"},
        )
        record_id = repo.insert_record(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            device_id=device_id,
            bot_id=bot_id,
            publish_id=publish_id,
            batch_id=batch_id,
            event_type="CREATE",
            result_status="PENDING",
            creator="u",
            modifier="u",
            trigger_source=None,
            publish_reason=None,
            result_message=None,
            extra_config={},
        )

        repo.update_result(
            record_id=record_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            result_status="SUCCESS",
            result_message="done",
            modifier="admin",
        )
        record = repo.get_by_id(record_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.result_status == "SUCCESS"
        assert record.result_message == "done"

    def test_insert_record_and_get_by_id(self):
        repo: PublishRecordRepository = (
            get_container().repository.publish_record_repository()
        )
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)
        device_id = _create_device()
        batch_repo = get_container().repository.publish_batch_repository()
        batch_id = batch_repo.insert_batch(
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

        record_id = repo.insert_record(
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
        assert record_id > 0

        record = repo.get_by_id(record_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, PublishRecordRecord)
        assert record.id == record_id
        assert record.device_id == device_id
        assert record.bot_id == bot_id
        assert record.publish_id == publish_id
        assert record.batch_id == batch_id
        assert record.event_type == "CREATE"
        assert record.result_status == "PENDING"
        assert record.tenant == TEST_TENANT
        assert record.env == TEST_ENV
        assert record.gmt_create is not None

    def test_update_result(self):
        repo = get_container().repository.publish_record_repository()
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)
        device_id = _create_device()
        batch_repo = get_container().repository.publish_batch_repository()
        batch_id = batch_repo.insert_batch(
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
        record_id = repo.insert_record(
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

        repo.update_result(
            record_id=record_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            result_status="SUCCESS",
            result_message="done",
            modifier="admin",
        )
        record = repo.get_by_id(record_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.result_status == "SUCCESS"
        assert record.result_message == "done"

    def test_list_by_batch_id(self):
        repo = get_container().repository.publish_record_repository()
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)
        device_id = _create_device()
        batch_repo = get_container().repository.publish_batch_repository()
        batch_id = batch_repo.insert_batch(
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
        repo.insert_record(
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

        records = repo.list_by_batch_id(batch_id, TEST_TENANT, TEST_ENV)
        assert len(records) == 1
        assert records[0].batch_id == batch_id

    def test_get_by_device_id_and_publish_id(self):
        repo = get_container().repository.publish_record_repository()
        bot_id = _create_bot()
        publish_id = _create_publish(bot_id)
        device_id = _create_device()
        batch_repo = get_container().repository.publish_batch_repository()
        batch_id = batch_repo.insert_batch(
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
        repo.insert_record(
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

        record = repo.get_by_device_id_and_publish_id(
            device_id, publish_id, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.device_id == device_id
        assert record.publish_id == publish_id
