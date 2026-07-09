from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.publish import (
    PublishRecord,
    PublishRepository,
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
        name="Publish Sqlite Bot",
    )


def _identity_fields() -> list[str]:
    return [
        "id",
        "tenant",
        "env",
        "domain",
        "bot_id",
        "publish_type",
        "status",
        "creator",
        "modifier",
        "name",
        "description",
        "publisher",
        "replica_desired",
        "batch_capacity",
        "batch_number",
        "cooldown_seconds",
        "config_version",
        "changelog",
        "extra_config",
    ]


class TestPublishSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: PublishRepository = get_container().repository.publish_repository()
        bot_id = _create_bot()
        extra = {"pipeline": "standard"}

        publish_id = repo.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="u",
            modifier="u",
            name="Sqlite Publish",
            description="ORM→ORM comparison",
            extra_config=extra,
        )
        assert publish_id > 0

        record = repo.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, PublishRecord)
        assert record.bot_id == bot_id
        assert record.publish_type == "CREATE"
        assert record.status == "PENDING"
        assert record.extra_config == extra
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.publish_repository()
        assert repo.get_by_id(99999999, TEST_TENANT, TEST_ENV) is None

    def test_deep_null_preservation(self):
        repo = get_container().repository.publish_repository()
        bot_id = _create_bot()

        publish_id = repo.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="u",
            modifier="u",
            name="Null Publish",
            description=None,
            extra_config=None,
        )
        record = repo.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.description is None
        assert record.extra_config == {}

    def test_deep_json_roundtrip(self):
        repo = get_container().repository.publish_repository()
        bot_id = _create_bot()
        extra = {"nested": {"key": "value"}, "list": [1, "two", None]}

        publish_id = repo.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="u",
            modifier="u",
            name="JSON Publish",
            extra_config=extra,
        )
        record = repo.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.extra_config == extra

    def test_deep_update_status(self):
        repo = get_container().repository.publish_repository()
        bot_id = _create_bot()

        publish_id = repo.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="orig",
            modifier="orig",
            name="Status Publish",
        )
        repo.update_status(
            publish_id=publish_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="updater",
        )
        record = repo.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "ACTIVE"
        assert record.name == "Status Publish"
        assert record.creator == "orig"

    def test_insert_publish_and_get_by_id(self):
        repo: PublishRepository = get_container().repository.publish_repository()
        bot_id = _create_bot()

        publish_id = repo.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
        )
        assert publish_id > 0

        record = repo.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, PublishRecord)
        assert record.id == publish_id
        assert record.bot_id == bot_id
        assert record.publish_type == "CREATE"
        assert record.status == "PENDING"
        assert record.tenant == TEST_TENANT
        assert record.env == TEST_ENV
        assert record.domain == "test_domain"
        assert record.creator == "test_user"
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_update_status(self):
        repo = get_container().repository.publish_repository()
        bot_id = _create_bot()

        publish_id = repo.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
        )
        repo.update_status(
            publish_id=publish_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="admin",
        )
        record = repo.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "ACTIVE"

    def test_soft_delete(self):
        repo = get_container().repository.publish_repository()
        bot_id = _create_bot()

        publish_id = repo.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
        )
        repo.soft_delete(
            publish_id=publish_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )
        result = repo.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert result is None

    def test_list_by_bot_id(self):
        repo = get_container().repository.publish_repository()
        bot_id = _create_bot()

        repo.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
        )
        records = repo.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert len(records) >= 1
        assert all(r.bot_id == bot_id for r in records)
