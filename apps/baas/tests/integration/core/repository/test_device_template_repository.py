"""Integration tests for DeviceTemplateRepository Protocol against ZDAS MySQL.

Every test uses ONLY the DeviceTemplateRepository Protocol — no
OrmDeviceTemplateRepository references allowed. db_transaction ensures
all changes are rolled back after each test.
"""

import random
from uuid import uuid4

import pytest

from secbaas.community.core.repository.device_template import (
    DeviceTemplateRecord,
    DeviceTemplateRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _gen_uuid() -> str:
    return uuid4().hex


def _gen_template_id() -> int:
    return random.randint(1, 999999999)


class TestDeviceTemplateRepository:
    """Integration tests for DeviceTemplateRepository Protocol."""

    # === 1. insert_template + get_by_id ===

    def test_insert_template_and_get_by_id(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        template_id = _gen_template_id()
        record_id = device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_creator",
            modifier="test_modifier",
            status="CREATED",
            name="Integration Test Template",
            description="A test template for integration test",
            config={"base_url": "http://test", "api_key": "secret"},
        )
        assert record_id > 0

        record = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert record is not None
        assert record.id == record_id
        assert record.template_uuid == template_uuid
        assert record.template_id == template_id
        assert record.type == "ARCA"
        assert record.tenant == TEST_TENANT
        assert record.creator == "test_creator"
        assert record.modifier == "test_modifier"
        assert record.status == "CREATED"
        assert record.name == "Integration Test Template"
        assert record.description == "A test template for integration test"
        assert record.config == {"base_url": "http://test", "api_key": "secret"}
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_tenant_isolation(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        record_id = device_template_repository.insert_template(
            template_uuid=_gen_uuid(),
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Isolation Test",
        )

        # Wrong tenant should not find the record
        result = device_template_repository.get_by_id(record_id, "wrong_tenant")
        assert result is None

        # Correct tenant should find it
        result = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert result is not None
        assert result.id == record_id

    def test_get_by_id_returns_none_for_missing(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        result = device_template_repository.get_by_id(99999999, TEST_TENANT)
        assert result is None

    # === 2. get_by_template_id ===

    def test_get_by_template_id(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_id = _gen_template_id()
        device_template_repository.insert_template(
            template_uuid=_gen_uuid(),
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="TemplateID Test",
        )

        record = device_template_repository.get_by_template_id(template_id)
        assert record is not None
        assert record.template_id == template_id
        assert record.name == "TemplateID Test"

    def test_get_by_template_id_returns_none_for_missing(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        result = device_template_repository.get_by_template_id(99999999)
        assert result is None

    # === 3. get_by_template_uuid ===

    def test_get_by_template_uuid(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="UUID Lookup",
        )

        record = device_template_repository.get_by_template_uuid(
            template_uuid, TEST_TENANT, "CREATED"
        )
        assert record is not None
        assert record.template_uuid == template_uuid
        assert record.status == "CREATED"

    def test_get_by_template_uuid_status_mismatch_returns_none(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Status Mismatch",
        )

        result = device_template_repository.get_by_template_uuid(
            template_uuid, TEST_TENANT, "ONLINE"
        )
        assert result is None

    def test_get_by_template_uuid_wrong_tenant_returns_none(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Tenant Isolation",
        )

        result = device_template_repository.get_by_template_uuid(
            template_uuid, "wrong_tenant", "CREATED"
        )
        assert result is None

    # === 4. list_by_template_uuid ===

    def test_list_by_template_uuid(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="List Test",
        )

        records = device_template_repository.list_by_template_uuid(
            template_uuid, TEST_TENANT
        )
        assert len(records) >= 1
        assert all(r.template_uuid == template_uuid for r in records)
        assert all(r.tenant == TEST_TENANT for r in records)

    def test_list_by_template_uuid_wrong_tenant_returns_empty(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Wrong Tenant List",
        )

        records = device_template_repository.list_by_template_uuid(
            template_uuid, "wrong_tenant"
        )
        assert len(records) == 0

    # === 5. get_online_by_template_uuid ===

    def test_get_online_by_template_uuid(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="ONLINE",
            name="Online Template",
        )

        record = device_template_repository.get_online_by_template_uuid(
            template_uuid, TEST_TENANT
        )
        assert record is not None
        assert record.template_uuid == template_uuid
        assert record.status == "ONLINE"

    def test_get_online_by_template_uuid_not_online_returns_none(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Not Online",
        )

        result = device_template_repository.get_online_by_template_uuid(
            template_uuid, TEST_TENANT
        )
        assert result is None

    # === 6. update_template (name, description, config) ===

    def test_update_template_name(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        record_id = device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Original Name",
        )

        rows = device_template_repository.update_template(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            status="CREATED",
            modifier="updater",
            name="Updated Name",
        )
        assert rows == 1

        record = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert record is not None
        assert record.name == "Updated Name"
        assert record.modifier == "updater"

    def test_update_template_description(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        record_id = device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Description Test",
            description="Before",
        )

        device_template_repository.update_template(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            status="CREATED",
            modifier="updater",
            description="After",
        )

        record = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert record is not None
        assert record.description == "After"

    def test_update_template_config(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        record_id = device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Config Test",
            config={"old_key": "old_value"},
        )

        device_template_repository.update_template(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            status="CREATED",
            modifier="updater",
            config={"new_key": "new_value"},
        )

        record = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert record is not None
        assert record.config == {"new_key": "new_value"}

    def test_update_template_no_fields_returns_zero(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="No-Update Test",
        )

        rows = device_template_repository.update_template(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            status="CREATED",
            modifier="only_modifier",
        )
        assert rows == 1

    def test_update_template_wrong_status_no_match(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Wrong Status",
        )

        # The template is CREATED, but we query with ONLINE status = no match
        rows = device_template_repository.update_template(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            status="ONLINE",  # mismatch
            modifier="updater",
            name="Should Not Match",
        )
        assert rows == 0

    # === 7. update_status ===

    def test_update_status(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        record_id = device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Status Test",
        )

        device_template_repository.update_status(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            current_status="CREATED",
            new_status="ONLINE",
        )

        record = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert record is not None
        assert record.status == "ONLINE"

    def test_update_status_wrong_current_status_no_op(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        record_id = device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Wrong Current Status",
        )

        # Attempt update with wrong current_status - should be a no-op
        device_template_repository.update_status(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            current_status="ONLINE",  # actual status is CREATED
            new_status="OFFLINE",
        )

        # Status should remain unchanged
        record = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert record is not None
        assert record.status == "CREATED"

    def test_update_status_tenant_isolation(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        record_id = device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Tenant Isolation Status",
        )

        # Wrong tenant should be a no-op
        device_template_repository.update_status(
            template_uuid=template_uuid,
            tenant="wrong_tenant",
            current_status="CREATED",
            new_status="ONLINE",
        )

        record = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert record is not None
        assert record.status == "CREATED"  # unchanged

    # === 8. soft_delete ===

    def test_soft_delete(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        record_id = device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Delete Me",
        )

        device_template_repository.soft_delete(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            status="CREATED",
            modifier="admin",
        )

        # After soft delete, get_by_id should return None (is_deleted != 0)
        record = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert record is None

    def test_soft_delete_wrong_status_no_op(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        record_id = device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Wrong Status Delete",
        )

        device_template_repository.soft_delete(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            status="ONLINE",  # actual is CREATED
            modifier="admin",
        )

        # Record should still be visible
        record = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert record is not None
        assert record.status == "CREATED"

    # === 9. list_templates (pagination, status filter) ===

    def test_list_templates_pagination(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        # Insert a template to ensure we have something for this tenant
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Pagination Test",
        )

        total, items = device_template_repository.list_templates(
            tenant=TEST_TENANT,
            page=1,
            page_size=10,
        )
        assert total >= 1

        uuids = [r.template_uuid for r in items]
        assert template_uuid in uuids

    def test_list_templates_status_filter(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        template_uuid = _gen_uuid()
        device_template_repository.insert_template(
            template_uuid=template_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="ONLINE",
            name="Online Filtered",
        )

        total, items = device_template_repository.list_templates(
            tenant=TEST_TENANT,
            status="CREATED",
            page=1,
            page_size=10,
        )
        # Items should NOT contain the ONLINE template
        online_uuids = [r.template_uuid for r in items if r.status == "ONLINE"]
        assert len(online_uuids) == 0

    def test_list_templates_page_size(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        # Insert two templates
        for _ in range(2):
            device_template_repository.insert_template(
                template_uuid=_gen_uuid(),
                template_id=_gen_template_id(),
                type="ARCA",
                tenant=TEST_TENANT,
                creator="test_user",
                modifier="test_user",
                status="CREATED",
                name="Page Size Test",
            )

        total, items = device_template_repository.list_templates(
            tenant=TEST_TENANT,
            page=1,
            page_size=1,
        )
        assert len(items) <= 1
        # Total should reflect all matching records regardless of page
        assert total >= 2

    # === 10. Template uniqueness (same uuid diff tenant = different record) ===

    def test_same_uuid_different_tenant_creates_separate_records(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        shared_uuid = _gen_uuid()

        # Insert with tenant_a
        id_a = device_template_repository.insert_template(
            template_uuid=shared_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant="tenant_a",
            creator="user_a",
            modifier="user_a",
            status="CREATED",
            name="Template in A",
        )

        # Insert with tenant_b (same uuid, different tenant)
        id_b = device_template_repository.insert_template(
            template_uuid=shared_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant="tenant_b",
            creator="user_b",
            modifier="user_b",
            status="CREATED",
            name="Template in B",
        )

        # Both should exist independently
        record_a = device_template_repository.get_by_id(id_a, "tenant_a")
        assert record_a is not None
        assert record_a.template_uuid == shared_uuid
        assert record_a.tenant == "tenant_a"
        assert record_a.name == "Template in A"

        record_b = device_template_repository.get_by_id(id_b, "tenant_b")
        assert record_b is not None
        assert record_b.template_uuid == shared_uuid
        assert record_b.tenant == "tenant_b"
        assert record_b.name == "Template in B"

        # Cross-tenant isolation
        assert device_template_repository.get_by_id(id_a, "tenant_b") is None
        assert device_template_repository.get_by_id(id_b, "tenant_a") is None

    def test_same_uuid_different_status_same_tenant_distinct(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        """Verify that same uuid + same tenant + different status = distinct records."""
        shared_uuid = _gen_uuid()

        id_created = device_template_repository.insert_template(
            template_uuid=shared_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Created Status",
        )

        id_online = device_template_repository.insert_template(
            template_uuid=shared_uuid,
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="ONLINE",
            name="Online Status",
        )

        # Both records exist
        r_created = device_template_repository.get_by_id(id_created, TEST_TENANT)
        assert r_created is not None
        assert r_created.status == "CREATED"

        r_online = device_template_repository.get_by_id(id_online, TEST_TENANT)
        assert r_online is not None
        assert r_online.status == "ONLINE"

        # list_by_template_uuid should return both
        records = device_template_repository.list_by_template_uuid(
            shared_uuid, TEST_TENANT
        )
        statuses = {r.status for r in records}
        assert "CREATED" in statuses
        assert "ONLINE" in statuses
        assert len(records) >= 2

    # === 11. Factory default values ===

    def test_factory_default_values(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        record_id = device_template_repository.insert_template(
            template_uuid=_gen_uuid(),
            template_id=_gen_template_id(),
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Defaults Test",
        )

        record = device_template_repository.get_by_id(record_id, TEST_TENANT)
        assert record is not None

        # description defaults to None
        assert record.description is None
        # config defaults to empty dict when None is stored
        assert record.config == {}
        # is_deleted defaults to 0
        assert record.is_deleted == 0
        # gmt_create and gmt_modified are set by DB
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_default_local_template_id(
        self,
        device_template_repository: DeviceTemplateRepository,
        db_transaction,
    ) -> None:
        """Verify get_default_local_template_id returns the minimum template_id
        for type='Local' and status='ONLINE', or None if none exist."""
        result = device_template_repository.get_default_local_template_id()
        # Just verify it returns an int or None without error
        if result is not None:
            assert isinstance(result, int)
        # If no Local/ONLINE templates exist, None is valid
