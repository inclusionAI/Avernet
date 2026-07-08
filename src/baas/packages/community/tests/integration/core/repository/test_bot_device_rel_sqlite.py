from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.bot_device_rel import (
    BotDeviceRelRecord,
    BotDeviceRelRepository,
)
from secbaas.core.repository.device import DeviceRepository
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
        name="Rel Sqlite Bot",
    )


def _create_device() -> str:
    repo = get_container().repository.device_repository()
    device_uuid = _generate_uuid()
    repo.insert_device(
        device_uuid=device_uuid,
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
    return device_uuid


class TestBotDeviceRelSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: BotDeviceRelRepository = (
            get_container().repository.bot_device_rel_repository()
        )
        bot_id = _create_bot()
        device_uuid = _create_device()

        rel_id = repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="u",
            modifier="u",
        )
        assert rel_id > 0

        record = repo.get_by_id(rel_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, BotDeviceRelRecord)
        assert record.bot_id == bot_id
        assert record.device_uuid == device_uuid
        assert record.tenant == TEST_TENANT
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.bot_device_rel_repository()
        assert repo.get_by_id(99999999, TEST_TENANT, TEST_ENV) is None

    def test_deep_list_by_bot_id(self):
        repo = get_container().repository.bot_device_rel_repository()
        bot_id = _create_bot()
        device_uuid_0 = _create_device()
        device_uuid_1 = _create_device()

        repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid_0,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="u",
            modifier="u",
        )
        repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid_1,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="u",
            modifier="u",
        )

        records = repo.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert len(records) == 2
        uuids = {r.device_uuid for r in records}
        assert device_uuid_0 in uuids
        assert device_uuid_1 in uuids

    def test_list_by_bot_id_empty(self):
        repo = get_container().repository.bot_device_rel_repository()
        bot_id = _create_bot()
        records = repo.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert records == []

    def test_soft_delete(self):
        repo = get_container().repository.bot_device_rel_repository()
        bot_id = _create_bot()
        device_uuid = _create_device()

        rel_id = repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )
        repo.soft_delete(
            rel_id=rel_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )
        result = repo.get_by_id(rel_id, TEST_TENANT, TEST_ENV)
        assert result is None

    def test_exists(self):
        repo = get_container().repository.bot_device_rel_repository()
        bot_id = _create_bot()
        device_uuid = _create_device()

        repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )
        assert (
            repo.exists(
                bot_id=bot_id, device_uuid=device_uuid, tenant=TEST_TENANT, env=TEST_ENV
            )
            is True
        )
        assert (
            repo.exists(
                bot_id=bot_id,
                device_uuid="nonexistent-device",
                tenant=TEST_TENANT,
                env=TEST_ENV,
            )
            is False
        )

    def test_count_by_bot_id(self):
        repo = get_container().repository.bot_device_rel_repository()
        bot_id = _create_bot()
        device_uuid_0 = _create_device()
        device_uuid_1 = _create_device()

        repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid_0,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )
        repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid_1,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )

        count = repo.count_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert count == 2
