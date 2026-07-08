import time
from uuid import uuid4

import pytest

from secbaas.core.repository.bot import BotRecord, BotRepository
from secbaas.core.repository.bot_device_rel import (
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


class TestBotRepositoryProtocol:
    """Integration tests for BotRepository Protocol against real ZDAS MySQL.

    Every test uses ONLY the BotRepository Protocol — no OrmBotRepository
    references allowed. db_transaction ensures all changes are rolled back.
    """

    def test_insert_and_get_by_id(self, bot_repository: BotRepository, db_transaction):
        bot_uuid = _generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Test Bot",
        )
        assert bot_id > 0

        record = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.id == bot_id
        assert record.bot_uuid == bot_uuid
        assert record.tenant == TEST_TENANT
        assert record.status == "PENDING"
        assert record.name == "Test Bot"

    def test_get_by_id_returns_none_for_missing(
        self, bot_repository: BotRepository, db_transaction
    ):
        result = bot_repository.get_by_id(99999999, TEST_TENANT, TEST_ENV)
        assert result is None

    def test_get_by_id_isolation_wrong_tenant(
        self, bot_repository: BotRepository, db_transaction
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Isolation Test",
        )

        result = bot_repository.get_by_id(bot_id, "wrong_tenant", TEST_ENV)
        assert result is None

        result = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert result is not None
        assert result.id == bot_id

    def test_get_by_bot_uuid(self, bot_repository: BotRepository, db_transaction):
        bot_uuid = _generate_uuid()
        bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="UUID Test",
            status="ACTIVE",
        )

        record = bot_repository.get_by_bot_uuid(
            bot_uuid, TEST_TENANT, TEST_ENV, status="ACTIVE"
        )
        assert record is not None
        assert record.bot_uuid == bot_uuid
        assert record.status == "ACTIVE"

    def test_get_by_bot_uuid_status_mismatch_returns_none(
        self, bot_repository: BotRepository, db_transaction
    ):
        bot_uuid = _generate_uuid()
        bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Status Mismatch",
            status="ACTIVE",
        )

        result = bot_repository.get_by_bot_uuid(
            bot_uuid, TEST_TENANT, TEST_ENV, status="PENDING"
        )
        assert result is None

    def test_get_active_by_bot_uuid(
        self, bot_repository: BotRepository, db_transaction
    ):
        bot_uuid = _generate_uuid()
        bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Active Test",
            status="ACTIVE",
        )

        record = bot_repository.get_active_by_bot_uuid(bot_uuid, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.bot_uuid == bot_uuid
        assert record.status == "ACTIVE"

    def test_list_by_bot_uuid(self, bot_repository: BotRepository, db_transaction):
        bot_uuid = _generate_uuid()
        bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="List Test",
        )

        records = bot_repository.list_by_bot_uuid(bot_uuid, TEST_TENANT, TEST_ENV)
        assert len(records) >= 1
        assert all(r.bot_uuid == bot_uuid for r in records)

    def test_list_bots(self, bot_repository: BotRepository, db_transaction):
        bot_uuid = _generate_uuid()
        bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="List Bots Test",
            status="PENDING",
        )

        total, items = bot_repository.list_bots(
            tenant=TEST_TENANT, env=TEST_ENV, page=1, page_size=10
        )
        assert total >= 1
        uuids = [r.bot_uuid for r in items]
        assert bot_uuid in uuids

    def test_list_bots_by_status(self, bot_repository: BotRepository, db_transaction):
        bot_uuid = _generate_uuid()
        bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Status Filter",
            status="PENDING",
        )

        total, items = bot_repository.list_bots(
            tenant=TEST_TENANT, env=TEST_ENV, status="ACTIVE", page=1, page_size=10
        )
        uuids = [r.bot_uuid for r in items]
        assert bot_uuid not in uuids

    def test_update_bot_name(self, bot_repository: BotRepository, db_transaction):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Original Name",
        )

        rows = bot_repository.update_bot(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            name="Updated Name",
        )
        assert rows == 1

        record = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.name == "Updated Name"

    def test_update_bot_description(
        self, bot_repository: BotRepository, db_transaction
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Desc Test",
            description="Before",
        )

        bot_repository.update_bot(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            description="After",
        )

        record = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.description == "After"

    def test_update_bot_extra_config(
        self, bot_repository: BotRepository, db_transaction
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Config Test",
            extra_config={"key": "value"},
        )

        bot_repository.update_bot(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            extra_config={"new_key": "new_value"},
        )

        record = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.extra_config == {"new_key": "new_value"}

    def test_update_bot_gmt_modified_changes(
        self, bot_repository: BotRepository, db_transaction
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Timestamp Test",
        )

        original = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert original is not None

        time.sleep(0.1)
        bot_repository.update_bot(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            name="Timestamp Changed",
        )

        updated = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert updated is not None
        assert updated.gmt_modified >= original.gmt_modified

    def test_update_status(self, bot_repository: BotRepository, db_transaction):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Status Test",
            status="PENDING",
        )

        bot_repository.update_status(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="admin",
        )

        record = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "ACTIVE"

    def test_soft_delete(self, bot_repository: BotRepository, db_transaction):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Delete Me",
        )

        bot_repository.soft_delete(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        record = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is None

    def test_get_by_id_including_deleted(
        self, bot_repository: BotRepository, db_transaction
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Including Deleted",
        )

        bot_repository.soft_delete(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        record = bot_repository.get_by_id_including_deleted(
            bot_id, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.id == bot_id

    def test_insert_bot_record_clone(
        self, bot_repository: BotRepository, db_transaction
    ):
        original_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Source Bot",
            status="ACTIVE",
        )

        clone_id = bot_repository.insert_bot_record(
            source_bot_id=original_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="PENDING",
        )
        assert clone_id > 0
        assert clone_id != original_id

        clone = bot_repository.get_by_id(clone_id, TEST_TENANT, TEST_ENV)
        assert clone is not None
        assert clone.status == "PENDING"

    def test_multiple_tenants_isolation(
        self, bot_repository: BotRepository, db_transaction
    ):
        tenant_a_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant="tenant_a",
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Bot A",
        )
        tenant_b_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant="tenant_b",
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Bot B",
        )

        result_a = bot_repository.get_by_id(tenant_a_id, "tenant_a", TEST_ENV)
        assert result_a is not None
        assert result_a.name == "Bot A"

        result_missing = bot_repository.get_by_id(tenant_a_id, "tenant_b", TEST_ENV)
        assert result_missing is None

        result_b = bot_repository.get_by_id(tenant_b_id, "tenant_b", TEST_ENV)
        assert result_b is not None
        assert result_b.name == "Bot B"

    def test_bot_record_fields_match_inserted(
        self, bot_repository: BotRepository, db_transaction
    ):
        bot_uuid = _generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_creator",
            modifier="test_modifier",
            status="PENDING",
            name="Field Match Bot",
            description="A test bot with all fields",
            template_uuid="template-abc-123",
            replica_desired=3,
            replica_minimum=2,
            replica_maximum=5,
            auto_scaling_enabled=1,
            sla_grade="premium",
            extra_config={"region": "cn-hangzhou"},
        )

        record = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.id == bot_id
        assert record.bot_uuid == bot_uuid
        assert record.tenant == TEST_TENANT
        assert record.env == TEST_ENV
        assert record.domain == "test_domain"
        assert record.creator == "test_creator"
        assert record.modifier == "test_modifier"
        assert record.status == "PENDING"
        assert record.name == "Field Match Bot"
        assert record.description == "A test bot with all fields"
        assert record.template_uuid == "template-abc-123"
        assert record.replica_desired == 3
        assert record.replica_minimum == 2
        assert record.replica_maximum == 5
        assert record.auto_scaling_enabled == 1
        assert record.sla_grade == "premium"
        assert record.extra_config == {"region": "cn-hangzhou"}

    def test_default_field_values(self, bot_repository: BotRepository, db_transaction):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Default Values",
        )

        record = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "PENDING"
        assert record.replica_desired == 1
        assert record.replica_minimum == 1
        assert record.replica_maximum == 10
        assert record.auto_scaling_enabled == 0
        assert record.sla_grade == "standard"
        assert record.description is None
        assert record.template_uuid is None

    def test_extra_config_none_default(
        self, bot_repository: BotRepository, db_transaction
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="No Extra Config",
        )

        record = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.extra_config == {} or record.extra_config is None

    def test_complete_destroy(self, bot_repository: BotRepository, db_transaction):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Destroy Me",
        )

        bot_repository.complete_destroy(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        record = bot_repository.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is None

        record = bot_repository.get_by_id_including_deleted(
            bot_id, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.id == bot_id
        assert record.status == "RELEASED"
        assert record.is_deleted == record.id

    def test_complete_destroy_idempotent(
        self, bot_repository: BotRepository, db_transaction
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Double Destroy",
        )

        bot_repository.complete_destroy(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        # Second call should not raise — already soft-deleted bot should be a no-op
        bot_repository.complete_destroy(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        record = bot_repository.get_by_id_including_deleted(
            bot_id, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.status == "RELEASED"
        assert record.is_deleted == record.id

    def test_complete_update_transfer_basic(
        self,
        bot_repository: BotRepository,
        device_repository: DeviceRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        old_bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Old Bot",
            status="ACTIVE",
        )
        new_bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="New Bot",
            status="PENDING",
        )

        device_uuid_1 = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid_1,
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
        device_uuid_2 = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid_2,
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

        bot_device_rel_repository.insert_rel(
            bot_id=old_bot_id,
            device_uuid=device_uuid_1,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )
        bot_device_rel_repository.insert_rel(
            bot_id=old_bot_id,
            device_uuid=device_uuid_2,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )

        bot_repository.complete_update_transfer(
            old_bot_id=old_bot_id,
            new_bot_id=new_bot_id,
            device_uuids=[device_uuid_1, device_uuid_2],
            domain="test_domain",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        # Old bot should be RELEASED and soft-deleted
        old_record = bot_repository.get_by_id(old_bot_id, TEST_TENANT, TEST_ENV)
        assert old_record is None

        old_del = bot_repository.get_by_id_including_deleted(
            old_bot_id, TEST_TENANT, TEST_ENV
        )
        assert old_del is not None
        assert old_del.status == "RELEASED"
        assert old_del.is_deleted == old_del.id

        # New bot should be ACTIVE
        new_record = bot_repository.get_by_id(new_bot_id, TEST_TENANT, TEST_ENV)
        assert new_record is not None
        assert new_record.status == "ACTIVE"

        # Old bot device rels should be gone
        old_rels = bot_device_rel_repository.list_by_bot_id(
            old_bot_id, TEST_TENANT, TEST_ENV
        )
        assert old_rels == []

        # New bot should own both devices
        new_rels = bot_device_rel_repository.list_by_bot_id(
            new_bot_id, TEST_TENANT, TEST_ENV
        )
        assert len(new_rels) == 2
        new_device_uuids = {r.device_uuid for r in new_rels}
        assert device_uuid_1 in new_device_uuids
        assert device_uuid_2 in new_device_uuids

    def test_complete_update_transfer_no_devices(
        self,
        bot_repository: BotRepository,
        bot_device_rel_repository: BotDeviceRelRepository,
        db_transaction,
    ):
        old_bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Old Empty Bot",
            status="ACTIVE",
        )
        new_bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="New Empty Bot",
            status="PENDING",
        )

        bot_repository.complete_update_transfer(
            old_bot_id=old_bot_id,
            new_bot_id=new_bot_id,
            device_uuids=[],
            domain="test_domain",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        # Old bot should be RELEASED and soft-deleted
        old_record = bot_repository.get_by_id(old_bot_id, TEST_TENANT, TEST_ENV)
        assert old_record is None

        old_del = bot_repository.get_by_id_including_deleted(
            old_bot_id, TEST_TENANT, TEST_ENV
        )
        assert old_del is not None
        assert old_del.status == "RELEASED"
        assert old_del.is_deleted == old_del.id

        # New bot should be ACTIVE
        new_record = bot_repository.get_by_id(new_bot_id, TEST_TENANT, TEST_ENV)
        assert new_record is not None
        assert new_record.status == "ACTIVE"

        # No device rels for new bot either
        new_rels = bot_device_rel_repository.list_by_bot_id(
            new_bot_id, TEST_TENANT, TEST_ENV
        )
        assert new_rels == []
