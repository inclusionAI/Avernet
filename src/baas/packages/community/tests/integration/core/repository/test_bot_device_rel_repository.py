from uuid import uuid4

import pytest

from secbaas.core.repository.bot import BotRepository
from secbaas.core.repository.bot_device_rel import (
    BotDeviceRelRecord,
    BotDeviceRelRepository,
)
from secbaas.core.repository.device import DeviceRepository
from secbaas.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


class TestBotDeviceRelRepositoryProtocol:
    """Integration tests for BotDeviceRelRepository Protocol against real ZDAS MySQL.

    Every test uses ONLY the BotDeviceRelRepository Protocol — no
    OrmBotDeviceRelRepository references allowed. db_transaction ensures
    all changes are rolled back.
    """

    # === Helpers ===

    @staticmethod
    def _create_bot(bot_repository: BotRepository) -> int:
        return bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Rel Test",
        )

    @staticmethod
    def _create_device(device_repository: DeviceRepository) -> tuple[int, str]:
        device_uuid = _generate_uuid()
        device_id = device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type="ARCA",
            provider_device_id=None,
            provider_device_props=None,
            extra_config=None,
        )
        return device_id, device_uuid

    @staticmethod
    def _create_rel(
        bot_device_rel_repository: BotDeviceRelRepository,
        bot_id: int,
        device_uuid: str,
    ) -> int:
        return bot_device_rel_repository.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )

    # === 1. insert_rel + get_by_id ===

    def test_insert_rel_and_get_by_id(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, device_uuid = self._create_device(device_repository)

        device = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert device is not None

        rel_id = self._create_rel(bot_device_rel_repository, bot_id, device.device_uuid)
        assert rel_id > 0

        record = bot_device_rel_repository.get_by_id(rel_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert isinstance(record, BotDeviceRelRecord)
        assert record.id == rel_id
        assert record.bot_id == bot_id
        assert record.device_uuid == device.device_uuid
        assert record.tenant == TEST_TENANT
        assert record.env == TEST_ENV
        assert record.domain == "test_domain"
        assert record.creator == "test_user"
        assert record.modifier == "test_user"
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    # === 2. list_by_bot_id ===

    def test_list_by_bot_id(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)

        id1, uuid1 = self._create_device(device_repository)
        id2, uuid2 = self._create_device(device_repository)

        dev1 = device_repository.get_by_id(id1, TEST_TENANT, TEST_ENV)
        dev2 = device_repository.get_by_id(id2, TEST_TENANT, TEST_ENV)
        assert dev1 is not None
        assert dev2 is not None

        self._create_rel(bot_device_rel_repository, bot_id, dev1.device_uuid)
        self._create_rel(bot_device_rel_repository, bot_id, dev2.device_uuid)

        rels = bot_device_rel_repository.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert len(rels) == 2
        device_uuids = {r.device_uuid for r in rels}
        assert dev1.device_uuid in device_uuids
        assert dev2.device_uuid in device_uuids
        for r in rels:
            assert r.bot_id == bot_id
            assert r.is_deleted == 0

    def test_list_by_bot_id_empty(
        self,
        bot_repository: BotRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        rels = bot_device_rel_repository.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert rels == []

    def test_list_by_bot_id_excludes_deleted(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        id1, _ = self._create_device(device_repository)
        dev1 = device_repository.get_by_id(id1, TEST_TENANT, TEST_ENV)
        assert dev1 is not None

        rel_id = self._create_rel(bot_device_rel_repository, bot_id, dev1.device_uuid)
        bot_device_rel_repository.soft_delete(
            rel_id=rel_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="test_user"
        )

        rels = bot_device_rel_repository.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert rels == []

    # === 3. get_by_device_uuid ===

    def test_get_by_device_uuid(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, _ = self._create_device(device_repository)
        device = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert device is not None

        self._create_rel(bot_device_rel_repository, bot_id, device.device_uuid)

        record = bot_device_rel_repository.get_by_device_uuid(
            device.device_uuid, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.device_uuid == device.device_uuid
        assert record.bot_id == bot_id

    def test_get_by_device_uuid_not_found(
        self, bot_device_rel_repository: BotDeviceRelRepository, db_transaction
    ):
        record = bot_device_rel_repository.get_by_device_uuid(
            "nonexistent-uuid", TEST_TENANT, TEST_ENV
        )
        assert record is None

    # === 4. soft_delete ===

    def test_soft_delete(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, _ = self._create_device(device_repository)
        device = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert device is not None

        rel_id = self._create_rel(bot_device_rel_repository, bot_id, device.device_uuid)

        record = bot_device_rel_repository.get_by_id(rel_id, TEST_TENANT, TEST_ENV)
        assert record is not None

        bot_device_rel_repository.soft_delete(
            rel_id=rel_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )

        record = bot_device_rel_repository.get_by_id(rel_id, TEST_TENANT, TEST_ENV)
        assert record is None

    # === 5. exists (True / False) ===

    def test_exists_true(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, _ = self._create_device(device_repository)
        device = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert device is not None

        self._create_rel(bot_device_rel_repository, bot_id, device.device_uuid)

        assert bot_device_rel_repository.exists(
            bot_id=bot_id,
            device_uuid=device.device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )

    def test_exists_false(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, device_uuid = self._create_device(device_repository)
        assert not bot_device_rel_repository.exists(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )

    def test_exists_false_after_soft_delete(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, _ = self._create_device(device_repository)
        device = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert device is not None

        rel_id = self._create_rel(bot_device_rel_repository, bot_id, device.device_uuid)
        bot_device_rel_repository.soft_delete(
            rel_id=rel_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )

        assert not bot_device_rel_repository.exists(
            bot_id=bot_id,
            device_uuid=device.device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )

    # === 6. count_by_bot_id ===

    def test_count_by_bot_id(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)

        id1, _ = self._create_device(device_repository)
        id2, _ = self._create_device(device_repository)

        dev1 = device_repository.get_by_id(id1, TEST_TENANT, TEST_ENV)
        dev2 = device_repository.get_by_id(id2, TEST_TENANT, TEST_ENV)
        assert dev1 is not None
        assert dev2 is not None

        self._create_rel(bot_device_rel_repository, bot_id, dev1.device_uuid)
        self._create_rel(bot_device_rel_repository, bot_id, dev2.device_uuid)

        count = bot_device_rel_repository.count_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert count == 2

    def test_count_by_bot_id_zero(
        self,
        bot_repository: BotRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        count = bot_device_rel_repository.count_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert count == 0

    def test_count_by_bot_id_excludes_deleted(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)

        id1, _ = self._create_device(device_repository)
        id2, _ = self._create_device(device_repository)

        dev1 = device_repository.get_by_id(id1, TEST_TENANT, TEST_ENV)
        dev2 = device_repository.get_by_id(id2, TEST_TENANT, TEST_ENV)
        assert dev1 is not None
        assert dev2 is not None

        rel_id = self._create_rel(bot_device_rel_repository, bot_id, dev1.device_uuid)
        self._create_rel(bot_device_rel_repository, bot_id, dev2.device_uuid)

        bot_device_rel_repository.soft_delete(
            rel_id=rel_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )

        count = bot_device_rel_repository.count_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert count == 1

    # === 7. soft_delete_by_bot_id ===

    def test_soft_delete_by_bot_id(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)

        id1, _ = self._create_device(device_repository)
        id2, _ = self._create_device(device_repository)

        dev1 = device_repository.get_by_id(id1, TEST_TENANT, TEST_ENV)
        dev2 = device_repository.get_by_id(id2, TEST_TENANT, TEST_ENV)
        assert dev1 is not None
        assert dev2 is not None

        self._create_rel(bot_device_rel_repository, bot_id, dev1.device_uuid)
        self._create_rel(bot_device_rel_repository, bot_id, dev2.device_uuid)

        count = bot_device_rel_repository.soft_delete_by_bot_id(
            bot_id=bot_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )
        assert count == 2

        rels = bot_device_rel_repository.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert rels == []

    def test_soft_delete_by_bot_id_no_rels(
        self,
        bot_repository: BotRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        count = bot_device_rel_repository.soft_delete_by_bot_id(
            bot_id=bot_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )
        assert count == 0

    # === 8. batch_insert_rels ===

    def test_batch_insert_rels(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)

        id1, _ = self._create_device(device_repository)
        id2, _ = self._create_device(device_repository)
        id3, _ = self._create_device(device_repository)

        dev1 = device_repository.get_by_id(id1, TEST_TENANT, TEST_ENV)
        dev2 = device_repository.get_by_id(id2, TEST_TENANT, TEST_ENV)
        dev3 = device_repository.get_by_id(id3, TEST_TENANT, TEST_ENV)
        assert dev1 is not None
        assert dev2 is not None
        assert dev3 is not None

        ids = bot_device_rel_repository.batch_insert_rels(
            bot_id=bot_id,
            device_uuids=[dev1.device_uuid, dev2.device_uuid, dev3.device_uuid],
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )
        assert len(ids) == 3
        for rid in ids:
            assert rid > 0

        rels = bot_device_rel_repository.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert len(rels) == 3
        device_uuids = {r.device_uuid for r in rels}
        assert dev1.device_uuid in device_uuids
        assert dev2.device_uuid in device_uuids
        assert dev3.device_uuid in device_uuids

        for rec in rels:
            assert rec.bot_id == bot_id
            assert rec.tenant == TEST_TENANT
            assert rec.env == TEST_ENV
            assert rec.domain == "test_domain"
            assert rec.is_deleted == 0

    def test_batch_insert_rels_empty_list(
        self,
        bot_repository: BotRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        ids = bot_device_rel_repository.batch_insert_rels(
            bot_id=bot_id,
            device_uuids=[],
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )
        assert ids == []

    # === 9. Tenant isolation ===

    def test_tenant_isolation_get_by_id(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, _ = self._create_device(device_repository)
        device = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert device is not None

        rel_id = self._create_rel(bot_device_rel_repository, bot_id, device.device_uuid)

        record = bot_device_rel_repository.get_by_id(rel_id, TEST_TENANT, TEST_ENV)
        assert record is not None

        record = bot_device_rel_repository.get_by_id(rel_id, "wrong_tenant", TEST_ENV)
        assert record is None

    def test_tenant_isolation_list_by_bot_id(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, _ = self._create_device(device_repository)
        device = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert device is not None

        self._create_rel(bot_device_rel_repository, bot_id, device.device_uuid)

        rels = bot_device_rel_repository.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert len(rels) == 1

        rels = bot_device_rel_repository.list_by_bot_id(
            bot_id, "wrong_tenant", TEST_ENV
        )
        assert rels == []

    def test_tenant_isolation_exists(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, _ = self._create_device(device_repository)
        device = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert device is not None

        self._create_rel(bot_device_rel_repository, bot_id, device.device_uuid)

        assert bot_device_rel_repository.exists(
            bot_id=bot_id,
            device_uuid=device.device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )
        assert not bot_device_rel_repository.exists(
            bot_id=bot_id,
            device_uuid=device.device_uuid,
            tenant="wrong_tenant",
            env=TEST_ENV,
        )

    def test_tenant_isolation_count_by_bot_id(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, _ = self._create_device(device_repository)
        device = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert device is not None

        self._create_rel(bot_device_rel_repository, bot_id, device.device_uuid)

        assert (
            bot_device_rel_repository.count_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
            == 1
        )
        assert (
            bot_device_rel_repository.count_by_bot_id(bot_id, "wrong_tenant", TEST_ENV)
            == 0
        )

    def test_tenant_isolation_soft_delete_by_bot_id(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        bot_id = self._create_bot(bot_repository)
        device_id, _ = self._create_device(device_repository)
        device = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert device is not None

        self._create_rel(bot_device_rel_repository, bot_id, device.device_uuid)

        count = bot_device_rel_repository.soft_delete_by_bot_id(
            bot_id=bot_id, tenant="wrong_tenant", env=TEST_ENV, modifier="admin"
        )
        assert count == 0

        rels = bot_device_rel_repository.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert len(rels) == 1

        count = bot_device_rel_repository.soft_delete_by_bot_id(
            bot_id=bot_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )
        assert count == 1
