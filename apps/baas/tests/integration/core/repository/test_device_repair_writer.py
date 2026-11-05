"""Integration tests for DeviceRepository.update_status_by_device_uuid.

Covers the admin device-repair writer: status and provider binding persisted in
one UPDATE, with the operator stamped as ``modifier``, and omitted fields left
unchanged. Uses only the DeviceRepository Protocol against the provisioned test
database; ``db_transaction`` rolls everything back.
"""

from uuid import uuid4

import pytest

from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


class TestUpdateStatusByDeviceUuidRepair:
    """Admin repair writer: provider binding and operator audit stamp."""

    def test_update_status_by_device_uuid_writes_provider_device_id_and_modifier(
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
            provider_type="arca",
            provider_device_id="sandbox-old",
        )

        rows = device_repository.update_status_by_device_uuid(
            device_uuid,
            TEST_TENANT,
            TEST_ENV,
            status="ACTIVE",
            modifier="ops-user",
            provider_device_id="sandbox-1",
        )
        assert rows == 1

        record = device_repository.get_by_device_uuid_only(device_uuid)
        assert record is not None
        assert record.status == "ACTIVE"
        assert record.provider_device_id == "sandbox-1"
        assert record.modifier == "ops-user"
        assert record.provider_type == "arca"  # untouched by this writer

    def test_update_status_by_device_uuid_omitted_fields_are_preserved(
        self, device_repository: DeviceRepository, db_transaction
    ):
        device_uuid = _generate_uuid()
        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="creator-user",
            modifier="creator-user",
            status="PENDING",
            provider_type="arca",
            provider_device_id="sandbox-keep",
        )

        rows = device_repository.update_status_by_device_uuid(
            device_uuid, TEST_TENANT, TEST_ENV, status="ACTIVE"
        )
        assert rows == 1

        record = device_repository.get_by_device_uuid_only(device_uuid)
        assert record is not None
        assert record.status == "ACTIVE"
        assert record.provider_device_id == "sandbox-keep"
        assert record.modifier == "creator-user"
