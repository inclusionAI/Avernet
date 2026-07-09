from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.device import (
    DeviceRecord,
    DeviceRepository,
)
from secbaas.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


def _identity_fields() -> list[str]:
    return [
        "id",
        "device_uuid",
        "tenant",
        "env",
        "domain",
        "is_deleted",
        "creator",
        "modifier",
        "status",
        "provider_type",
        "provider_device_id",
        "provider_device_props",
        "extra_config",
    ]


class TestDeviceSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: DeviceRepository = get_container().repository.device_repository()
        device_uuid = _generate_uuid()
        props = {"region": "cn-hangzhou", "tier": "premium"}

        device_id = repo.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="u",
            modifier="u",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-sqlite",
            provider_device_props=props,
            extra_config={"key": "v"},
        )
        assert device_id > 0

        record = repo.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, DeviceRecord)
        assert record.device_uuid == device_uuid
        assert record.tenant == TEST_TENANT
        assert record.status == "PENDING"
        assert record.provider_device_props == props
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.device_repository()
        assert repo.get_by_id(99999999, TEST_TENANT, TEST_ENV) is None

    def test_deep_null_preservation(self):
        repo = get_container().repository.device_repository()
        device_uuid = _generate_uuid()

        device_id = repo.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="u",
            modifier="u",
            status="PENDING",
            provider_type=None,
            provider_device_id=None,
            provider_device_props={"region": "test"},
            extra_config=None,
        )
        record = repo.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.provider_type is None
        assert record.provider_device_id is None
        assert record.extra_config == {}

    def test_deep_json_roundtrip(self):
        repo = get_container().repository.device_repository()
        device_uuid = _generate_uuid()
        props = {"nested": {"key": "value"}, "list": [1, "two", None]}

        device_id = repo.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="u",
            modifier="u",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-json",
            provider_device_props=props,
            extra_config={},
        )
        record = repo.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.provider_device_props == props

    def test_deep_update_status(self):
        repo = get_container().repository.device_repository()
        device_uuid = _generate_uuid()

        device_id = repo.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="orig",
            modifier="orig",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-upd",
            provider_device_props={"region": "test"},
            extra_config={},
        )
        repo.update_status(
            device_id=device_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
        )
        record = repo.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "ACTIVE"
        assert record.device_uuid == device_uuid
        assert record.creator == "orig"

    def test_get_by_device_uuid(self):
        repo = get_container().repository.device_repository()
        device_uuid = _generate_uuid()

        repo.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-equiv",
            provider_device_props={"region": "test"},
            extra_config={},
        )

        record = repo.get_by_device_uuid(device_uuid, TEST_TENANT, TEST_ENV, "PENDING")
        assert record is not None
        assert record.device_uuid == device_uuid

    def test_get_by_device_uuid_nonexistent(self):
        repo = get_container().repository.device_repository()
        assert (
            repo.get_by_device_uuid(
                "nonexistent-device-uuid", TEST_TENANT, TEST_ENV, "PENDING"
            )
            is None
        )

    def test_soft_delete(self):
        repo = get_container().repository.device_repository()
        device_uuid = _generate_uuid()

        device_id = repo.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-equiv",
            provider_device_props={"region": "test"},
            extra_config={},
        )
        repo.soft_delete(
            device_id=device_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )
        result = repo.get_by_id(device_id, TEST_TENANT, TEST_ENV)
        assert result is None

    def test_soft_delete_by_device_uuid(self):
        repo = get_container().repository.device_repository()
        device_uuid = _generate_uuid()

        repo.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-equiv",
            provider_device_props={"region": "test"},
            extra_config={},
        )
        count = repo.soft_delete_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV, "admin"
        )
        assert count == 1

    def test_list_devices_pagination(self):
        repo = get_container().repository.device_repository()
        device_uuid_0 = _generate_uuid()
        device_uuid_1 = _generate_uuid()

        repo.insert_device(
            device_uuid=device_uuid_0,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-equiv-0",
            provider_device_props={"region": "test"},
            extra_config={},
        )
        repo.insert_device(
            device_uuid=device_uuid_1,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test",
            creator="test_user",
            modifier="test_user",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-equiv-1",
            provider_device_props={"region": "test"},
            extra_config={},
        )

        total, records = repo.list_devices(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            page=1,
            page_size=10,
        )
        assert total >= 2
        ids = {r.device_uuid for r in records}
        assert device_uuid_0 in ids or device_uuid_1 in ids
