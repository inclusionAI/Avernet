import time
from uuid import uuid4

import pytest

from secbaas.core.repository.device import DeviceRecord, DeviceRepository
from secbaas.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


class TestDeviceRepositoryProtocol:
    """Integration tests for DeviceRepository Protocol against real ZDAS MySQL.

    Every test uses ONLY the DeviceRepository Protocol — no OrmDeviceRepository
    references allowed. db_transaction ensures all changes are rolled back.
    """

    # === 1. insert_device + get_by_id (all fields match) ===

    def test_insert_and_get_by_id(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_id = device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-123",
            provider_device_props={"region": "cn-hangzhou"},
            extra_config={"key": "value"},
        )
        assert device_id > 0

        record = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.id == device_id
        assert record.device_uuid == device_uuid
        assert record.tenant == TEST_TENANT
        assert record.env == TEST_ENV
        assert record.domain == "test_domain"
        assert record.creator == "test_user"
        assert record.modifier == "test_user"
        assert record.status == "PENDING"
        assert record.provider_type == "ARCA"
        assert record.provider_device_id == "sandbox-123"
        assert record.provider_device_props == {"region": "cn-hangzhou"}
        assert record.extra_config == {"key": "value"}
        assert record.is_deleted == 0

    # === 2. get_by_id returns None for missing ===

    def test_get_by_id_returns_none_for_missing(
        self, device_repository: DeviceRepository, db_transaction
    ):
        result = device_repository.get_by_id(99999999, TEST_TENANT, TEST_ENV)
        assert result is None

    # === 3. get_by_id isolation wrong tenant ===

    def test_get_by_id_isolation_wrong_tenant(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        result = device_repository.get_by_id(device_id, "wrong_tenant", TEST_ENV)
        assert result is None

        result = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert result is not None
        assert result.id == device_id

    # === 4. get_by_ids (batch lookup) ===

    def test_get_by_ids(self, device_repository: DeviceRepository, db_transaction):
        uuid1 = _generate_uuid()
        uuid2 = _generate_uuid()
        uuid3 = _generate_uuid()

        id1 = device_repository.insert_device(
            device_uuid=uuid1,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )
        id2 = device_repository.insert_device(
            device_uuid=uuid2,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type="ARCA",
        )
        id3 = device_repository.insert_device(
            device_uuid=uuid3,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        result = device_repository.get_by_ids([id1, id2, id3], TEST_TENANT, TEST_ENV)
        assert len(result) == 3
        assert id1 in result
        assert id2 in result
        assert id3 in result
        assert result[id1].device_uuid == uuid1
        assert result[id2].device_uuid == uuid2
        assert result[id3].device_uuid == uuid3

    def test_get_by_ids_empty_list(
        self, device_repository: DeviceRepository, db_transaction
    ):
        result = device_repository.get_by_ids([], TEST_TENANT, TEST_ENV)
        assert result == {}

    def test_get_by_ids_partial_match(
        self, device_repository: DeviceRepository, db_transaction
    ):
        uuid1 = _generate_uuid()
        id1 = device_repository.insert_device(
            device_uuid=uuid1,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        result = device_repository.get_by_ids([id1, 99999999], TEST_TENANT, TEST_ENV)
        assert len(result) == 1
        assert id1 in result

    # === 5. get_by_device_uuid ===

    def test_get_by_device_uuid(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type="ARCA",
        )

        record = device_repository.get_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV, status="ACTIVE"
        )
        assert record is not None
        assert record.device_uuid == device_uuid
        assert record.status == "ACTIVE"

    def test_get_by_device_uuid_status_mismatch_returns_none(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type=None,
        )

        result = device_repository.get_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV, status="PENDING"
        )
        assert result is None

    # === 6. get_by_device_uuid_only ===

    def test_get_by_device_uuid_only(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type=None,
        )

        record = device_repository.get_by_device_uuid_only(device_uuid)
        assert record is not None
        assert record.device_uuid == device_uuid
        assert record.tenant == TEST_TENANT

    def test_get_by_device_uuid_only_not_found(
        self, device_repository: DeviceRepository, db_transaction
    ):
        result = device_repository.get_by_device_uuid_only("nonexistent-uuid")
        assert result is None

    # === 7. list_by_device_uuid ===

    def test_list_by_device_uuid(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        records = device_repository.list_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV
        )
        assert len(records) >= 1
        assert all(r.device_uuid == device_uuid for r in records)

    # === 8. get_active_by_device_uuid ===

    def test_get_active_by_device_uuid(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type="ARCA",
        )

        record = device_repository.get_active_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.device_uuid == device_uuid
        assert record.status == "ACTIVE"

    def test_get_active_by_device_uuid_wrong_status(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        record = device_repository.get_active_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV
        )
        assert record is None

    # === 9. get_active_or_updating_by_device_uuid ===

    def test_get_active_or_updating_by_device_uuid_active(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type=None,
        )

        record = device_repository.get_active_or_updating_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.status == "ACTIVE"

    def test_get_active_or_updating_by_device_uuid_updating(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="UPDATING",
            provider_type=None,
        )

        record = device_repository.get_active_or_updating_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV
        )
        assert record is not None
        assert record.status == "UPDATING"

    def test_get_active_or_updating_by_device_uuid_pending_returns_none(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        record = device_repository.get_active_or_updating_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV
        )
        assert record is None

    # === 10. update_device (name, provider fields, extra_config, status) ===

    def test_update_device_provider_fields(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-001",
            provider_device_props={"region": "cn-hangzhou"},
            extra_config={"version": "1.0"},
        )

        rows = device_repository.update_device(
            device_id=device_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="updater",
            provider_type="LOCAL",
            provider_device_id="local-device-001",
            provider_device_props={"region": "cn-beijing", "cpu": 4},
            extra_config={"version": "2.0"},
        )
        assert rows == 1

        record = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.modifier == "updater"
        assert record.provider_type == "LOCAL"
        assert record.provider_device_id == "local-device-001"
        assert record.provider_device_props == {"region": "cn-beijing", "cpu": 4}
        assert record.extra_config == {"version": "2.0"}

    def test_update_device_status_only(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        device_repository.update_device(
            device_id=device_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="FAILED",
        )

        record = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "FAILED"

    def test_update_device_gmt_modified_changes(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        original = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert original is not None

        time.sleep(0.1)
        device_repository.update_device(
            device_id=device_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
        )

        updated = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert updated is not None
        assert updated.gmt_modified >= original.gmt_modified

    # === 11. update_status ===

    def test_update_status(self, device_repository: DeviceRepository, db_transaction):
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        device_repository.update_status(
            device_id=device_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
        )

        record = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "ACTIVE"

    # === 12. soft_delete ===

    def test_soft_delete(self, device_repository: DeviceRepository, db_transaction):
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        device_repository.soft_delete(
            device_id=device_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        record = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is None

    # === 13. soft_delete_by_device_uuid ===

    def test_soft_delete_by_device_uuid(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        rows = device_repository.soft_delete_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV, modifier="admin"
        )
        assert rows == 1

        record = device_repository.get_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV, status="PENDING"
        )
        assert record is None

    def test_soft_delete_by_device_uuid_nonexistent(
        self, device_repository: DeviceRepository, db_transaction
    ):
        rows = device_repository.soft_delete_by_device_uuid(
            "nonexistent-uuid", TEST_TENANT, TEST_ENV, modifier="admin"
        )
        assert rows == 0

    # === 14. update_status_by_device_uuid ===

    def test_update_status_by_device_uuid(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        rows = device_repository.update_status_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV, status="FAILED"
        )
        assert rows == 1

        record = device_repository.get_active_or_updating_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV
        )
        assert record is None  # FAILED is not ACTIVE or UPDATING

    def test_update_status_by_device_uuid_nonexistent(
        self, device_repository: DeviceRepository, db_transaction
    ):
        rows = device_repository.update_status_by_device_uuid(
            "nonexistent-uuid", TEST_TENANT, TEST_ENV, status="ACTIVE"
        )
        assert rows == 0

    # === 15. list_devices (pagination, status filter) ===

    def test_list_devices(self, device_repository: DeviceRepository, db_transaction):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        total, items = device_repository.list_devices(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            page=1,
            page_size=10,
        )
        assert total >= 1
        uuids = [r.device_uuid for r in items]
        assert device_uuid in uuids

    def test_list_devices_by_status(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        total, items = device_repository.list_devices(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            page=1,
            page_size=10,
        )
        uuids = [r.device_uuid for r in items]
        assert device_uuid not in uuids

    def test_list_devices_pagination(
        self, device_repository: DeviceRepository, db_transaction
    ):
        uuid1 = _generate_uuid()
        uuid2 = _generate_uuid()
        device_repository.insert_device(
            device_uuid=uuid1,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )
        device_repository.insert_device(
            device_uuid=uuid2,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        total, items = device_repository.list_devices(
            tenant=TEST_TENANT, env=TEST_ENV, page=1, page_size=1
        )
        assert total >= 2
        assert len(items) == 1

    # === 16. Factory defaults test ===

    def test_default_field_values(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        record = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "PENDING"
        assert record.provider_type is None
        assert record.provider_device_id is None
        assert record.provider_device_props == {}
        assert record.extra_config == {}
        assert record.is_deleted == 0
        assert record.device_uuid is not None
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_provider_device_props_and_extra_config_default(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        record = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.provider_device_props == {}
        assert record.extra_config == {}

    # === 17. Tenant isolation test ===

    def test_multiple_tenants_isolation(
        self, device_repository: DeviceRepository, db_transaction
    ):
        uuid_a = _generate_uuid()
        uuid_b = _generate_uuid()

        id_a = device_repository.insert_device(
            device_uuid=uuid_a,
            tenant="tenant_a",
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="device-a",
        )
        id_b = device_repository.insert_device(
            device_uuid=uuid_b,
            tenant="tenant_b",
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type="LOCAL",
            provider_device_id="device-b",
        )

        # Can see own data
        record_a = device_repository.get_by_id(id_a, "tenant_a", TEST_ENV)
        assert record_a is not None
        assert record_a.device_uuid == uuid_a
        assert record_a.provider_device_id == "device-a"

        # Cannot see other tenant's data
        result_missing = device_repository.get_by_id(id_a, "tenant_b", TEST_ENV)
        assert result_missing is None

        record_b = device_repository.get_by_id(id_b, "tenant_b", TEST_ENV)
        assert record_b is not None
        assert record_b.device_uuid == uuid_b
        assert record_b.provider_device_id == "device-b"

    def test_list_devices_tenant_isolation(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant="tenant_a",
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type=None,
        )

        # Querying tenant_b should not see tenant_a's devices
        total_b, items_b = device_repository.list_devices(
            tenant="tenant_b", env=TEST_ENV, page=1, page_size=10
        )
        assert all(r.tenant == "tenant_b" for r in items_b)

    # === Edge Cases ===

    def test_device_record_fields_all_populated(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_id = device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="production",
            creator="creator_001",
            modifier="modifier_001",
            status="ACTIVE",
            provider_type="ARCA",
            provider_device_id="sandbox-prod-999",
            provider_device_props={"region": "us-west-2", "tier": "premium"},
            extra_config={"monitoring": True, "log_level": "debug"},
        )

        record = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert isinstance(record, DeviceRecord)
        assert record.id == device_id
        assert record.device_uuid == device_uuid
        assert record.tenant == TEST_TENANT
        assert record.env == TEST_ENV
        assert record.domain == "production"
        assert record.creator == "creator_001"
        assert record.modifier == "modifier_001"
        assert record.status == "ACTIVE"
        assert record.provider_type == "ARCA"
        assert record.provider_device_id == "sandbox-prod-999"
        assert record.provider_device_props == {
            "region": "us-west-2",
            "tier": "premium",
        }
        assert record.extra_config == {"monitoring": True, "log_level": "debug"}
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_update_device_err_msg(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="FAILED",
            provider_type=None,
        )

        rows = device_repository.update_device(
            device_id=device_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            err_msg="Deployment timeout after 30s",
        )
        assert rows == 1

        record = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        # err_msg is not a field on DeviceRecord (convenience access pattern differs),
        # but the update should succeed silently

    # === batch_update_status_to_offline ===

    def test_batch_update_status_to_offline_basic(
        self, device_repository: DeviceRepository, db_transaction
    ):
        id1 = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type=None,
        )
        id2 = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type=None,
        )

        count = device_repository.batch_update_status_to_offline([id1, id2], TEST_ENV)
        assert count == 2

        for device_id in [id1, id2]:
            record = device_repository.get_by_id(device_id, TEST_TENANT, TEST_ENV)
            assert record is not None
            assert record.status == "OFFLINE"
            assert record.gmt_modified >= record.gmt_create

    def test_batch_update_status_to_offline_empty(
        self, device_repository: DeviceRepository, db_transaction
    ):
        count = device_repository.batch_update_status_to_offline([], TEST_ENV)
        assert count == 0

    def test_batch_update_status_to_offline_partial(
        self, device_repository: DeviceRepository, db_transaction
    ):
        id1 = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type=None,
        )
        id2 = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type=None,
        )
        id3 = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type=None,
        )

        count = device_repository.batch_update_status_to_offline([id1, id3], TEST_ENV)
        assert count == 2

        # Verify id1 and id3 are OFFLINE
        record1 = device_repository.get_by_id(id1, TEST_TENANT, TEST_ENV)
        assert record1 is not None
        assert record1.status == "OFFLINE"

        record3 = device_repository.get_by_id(id3, TEST_TENANT, TEST_ENV)
        assert record3 is not None
        assert record3.status == "OFFLINE"

        # Verify id2 is still ACTIVE (unchanged)
        record2 = device_repository.get_by_id(id2, TEST_TENANT, TEST_ENV)
        assert record2 is not None
        assert record2.status == "ACTIVE"

    # === list_active_local_devices_by_machine_user ===

    def test_list_active_local_devices_by_machine_user(
        self, device_repository: DeviceRepository, db_transaction
    ):
        unique_machine = _generate_uuid()[:12]
        unique_user = _generate_uuid()[:8]
        unique_pdid = f"sandbox-123--{unique_machine}--{unique_user}@template_xyz"

        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type="local",
            provider_device_id=unique_pdid,
        )

        results = device_repository.list_active_local_devices_by_machine_user(
            machine_id=unique_machine,
            user_id=unique_user,
            env=TEST_ENV,
        )
        assert len(results) == 1
        assert results[0].id == device_id
        assert results[0].provider_type == "local"
        assert results[0].provider_device_id == unique_pdid
        assert results[0].status == "ACTIVE"

    # === get_by_provider_device_id_prefix ===

    def test_get_by_provider_device_id_prefix(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_id = device_repository.insert_device(
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type="ARCA",
            provider_device_id="sandbox-abc123",
        )

        record = device_repository.get_by_provider_device_id_prefix(
            prefix="sandbox-abc", env=TEST_ENV
        )
        assert record is not None
        assert record.id == device_id
        assert record.provider_device_id == "sandbox-abc123"

        # Query with nonexistent prefix returns None
        missing = device_repository.get_by_provider_device_id_prefix(
            prefix="nonexistent-prefix", env=TEST_ENV
        )
        assert missing is None
