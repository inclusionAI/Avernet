from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.bot import BotRecord, BotRepository
from secbaas.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


def _identity_fields() -> list[str]:
    return [
        "id",
        "bot_uuid",
        "tenant",
        "env",
        "domain",
        "is_deleted",
        "creator",
        "modifier",
        "status",
        "name",
        "description",
        "template_uuid",
        "replica_desired",
        "replica_minimum",
        "replica_maximum",
        "auto_scaling_enabled",
        "sla_grade",
        "extra_config",
    ]


class TestBotSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: BotRepository = get_container().repository.bot_repository()
        bot_uuid = _generate_uuid()
        extra = {"region": "cn-hangzhou", "tier": "premium"}

        bot_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="u",
            modifier="u",
            name="Sqlite Bot",
            description="ORM→ORM comparison",
            extra_config=extra,
            sla_grade="standard",
        )
        assert bot_id > 0

        record = repo.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, BotRecord)
        assert record.bot_uuid == bot_uuid
        assert record.tenant == TEST_TENANT
        assert record.name == "Sqlite Bot"
        assert record.extra_config == extra
        assert record.status == "PENDING"
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.bot_repository()
        assert repo.get_by_id(99999999, TEST_TENANT, TEST_ENV) is None

    def test_deep_null_preservation(self):
        repo = get_container().repository.bot_repository()
        bot_uuid = _generate_uuid()

        bot_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="u",
            modifier="u",
            name="Null Sqlite Bot",
            description=None,
            template_uuid=None,
            extra_config=None,
        )
        record = repo.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.description is None
        assert record.template_uuid is None
        assert record.extra_config == {}

    def test_deep_json_roundtrip(self):
        repo = get_container().repository.bot_repository()
        bot_uuid = _generate_uuid()
        extra = {"nested": {"key": "value"}, "list": [1, "two", None]}

        bot_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="u",
            modifier="u",
            name="JSON Sqlite Bot",
            extra_config=extra,
        )
        record = repo.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.extra_config == extra

    def test_deep_update_status(self):
        repo = get_container().repository.bot_repository()
        bot_uuid = _generate_uuid()

        bot_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="orig",
            modifier="orig",
            name="Status Sqlite Bot",
        )
        repo.update_status(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="updater",
        )
        record = repo.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "ACTIVE"
        assert record.name == "Status Sqlite Bot"
        assert record.creator == "orig"

    def test_soft_delete(self):
        repo = get_container().repository.bot_repository()
        bot_uuid = _generate_uuid()

        bot_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Equiv Bot",
        )
        repo.soft_delete(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )
        record = repo.get_by_id_including_deleted(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.is_deleted == bot_id

        result = repo.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert result is None

    def test_list_bots_pagination(self):
        repo = get_container().repository.bot_repository()
        bot_uuid_0 = _generate_uuid()
        bot_uuid_1 = _generate_uuid()

        repo.insert_bot(
            bot_uuid=bot_uuid_0,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Equiv Bot 0",
        )
        repo.insert_bot(
            bot_uuid=bot_uuid_1,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Equiv Bot 1",
        )

        total, records = repo.list_bots(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            page=1,
            page_size=10,
        )
        assert total >= 2
        names = {r.name for r in records}
        assert "Equiv Bot 0" in names or "Equiv Bot 1" in names
