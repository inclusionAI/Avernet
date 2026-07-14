from __future__ import annotations

import random
from uuid import uuid4

import pytest

from secbaas.community.bootstrap import get_container
from secbaas.community.core.repository.device_template import (
    DeviceTemplateRecord,
    DeviceTemplateRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


class TestDeviceTemplateSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: DeviceTemplateRepository = (
            get_container().repository.device_template_repository()
        )
        template_uuid = _generate_uuid()
        template_id = random.randint(1, 999999)
        config = {"base_url": "test"}

        record_id = repo.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="u",
            modifier="u",
            status="CREATED",
            name="Sqlite Template",
            description="ORM→ORM comparison",
            config=config,
        )
        assert record_id > 0

        record = repo.get_by_id(record_id, TEST_TENANT)
        assert isinstance(record, DeviceTemplateRecord)
        assert record.template_uuid == template_uuid
        assert record.template_id == template_id
        assert record.status == "CREATED"
        assert record.config == config
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.device_template_repository()
        assert repo.get_by_id(99999999, TEST_TENANT) is None

    def test_deep_null_preservation(self):
        repo = get_container().repository.device_template_repository()
        template_uuid = _generate_uuid()
        template_id = random.randint(1, 999999)

        repo.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="u",
            modifier="u",
            status="CREATED",
            name="Null Template",
            description=None,
            config={"base_url": "test"},
        )
        record = repo.get_by_template_uuid(template_uuid, TEST_TENANT, "CREATED")
        assert record is not None
        assert record.description is None

    def test_deep_json_roundtrip(self):
        repo = get_container().repository.device_template_repository()
        template_uuid = _generate_uuid()
        template_id = random.randint(1, 999999)
        config = {"nested": {"key": "value"}, "list": [1, "two", None]}

        repo.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="u",
            modifier="u",
            status="CREATED",
            name="JSON Template",
            config=config,
        )
        record = repo.get_by_template_uuid(template_uuid, TEST_TENANT, "CREATED")
        assert record is not None
        assert record.config == config

    def test_deep_update_status(self):
        repo = get_container().repository.device_template_repository()
        template_uuid = _generate_uuid()
        template_id = random.randint(1, 999999)

        repo.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="orig",
            modifier="orig",
            status="CREATED",
            name="Status Template",
            config={"base_url": "test"},
        )
        repo.update_status(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            current_status="CREATED",
            new_status="ONLINE",
        )
        record = repo.get_by_template_uuid(template_uuid, TEST_TENANT, "ONLINE")
        assert record is not None
        assert record.status == "ONLINE"
        assert record.name == "Status Template"
        assert record.creator == "orig"

    def test_insert_template_and_get_by_id(self):
        repo: DeviceTemplateRepository = (
            get_container().repository.device_template_repository()
        )
        template_uuid = _generate_uuid()
        template_id = random.randint(100000, 999999)

        record_id = repo.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Equiv Template",
            description="Equivalence test template",
            config={"base_url": "test"},
        )
        assert record_id > 0

        record = repo.get_by_id(record_id, TEST_TENANT)
        assert isinstance(record, DeviceTemplateRecord)
        assert record.id == record_id
        assert record.template_uuid == template_uuid
        assert record.template_id == template_id
        assert record.type == "ARCA"
        assert record.tenant == TEST_TENANT
        assert record.status == "CREATED"
        assert record.name == "Equiv Template"
        assert record.description == "Equivalence test template"
        assert record.config == {"base_url": "test"}
        assert record.creator == "test_user"
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_template_uuid(self):
        repo = get_container().repository.device_template_repository()
        template_uuid = _generate_uuid()
        template_id = random.randint(100000, 999999)

        repo.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Equiv Template",
            description="Equivalence test template",
            config={"base_url": "test"},
        )

        record = repo.get_by_template_uuid(template_uuid, TEST_TENANT, "CREATED")
        assert record is not None
        assert record.template_uuid == template_uuid

    def test_get_by_template_uuid_nonexistent(self):
        repo = get_container().repository.device_template_repository()
        assert (
            repo.get_by_template_uuid("nonexistent-tpl-uuid", TEST_TENANT, "CREATED")
            is None
        )

    def test_update_template(self):
        repo = get_container().repository.device_template_repository()
        template_uuid = _generate_uuid()
        template_id = random.randint(100000, 999999)

        repo.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Original Name",
            description="Original desc",
            config={"base_url": "test"},
        )
        repo.update_template(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            status="CREATED",
            modifier="admin",
            name="Updated Name",
            description="Updated desc",
        )

        record = repo.get_by_template_uuid(template_uuid, TEST_TENANT, "CREATED")
        assert record is not None
        assert record.name == "Updated Name"
        assert record.description == "Updated desc"

    def test_update_status(self):
        repo = get_container().repository.device_template_repository()
        template_uuid = _generate_uuid()
        template_id = random.randint(100000, 999999)

        repo.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Equiv Template",
            description="Status change test",
            config={"base_url": "test"},
        )
        repo.update_status(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            current_status="CREATED",
            new_status="ONLINE",
        )

        record = repo.get_by_template_uuid(template_uuid, TEST_TENANT, "ONLINE")
        assert record is not None
        assert record.status == "ONLINE"

    def test_soft_delete(self):
        repo = get_container().repository.device_template_repository()
        template_uuid = _generate_uuid()
        template_id = random.randint(100000, 999999)

        repo.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="Equiv Template",
            description="Soft delete test",
            config={"base_url": "test"},
        )
        repo.soft_delete(
            template_uuid=template_uuid,
            tenant=TEST_TENANT,
            status="CREATED",
            modifier="admin",
        )
        result = repo.get_by_template_uuid(template_uuid, TEST_TENANT, "CREATED")
        assert result is None

    def test_list_templates(self):
        repo = get_container().repository.device_template_repository()
        template_uuid = _generate_uuid()
        template_id = random.randint(100000, 999999)

        repo.insert_template(
            template_uuid=template_uuid,
            template_id=template_id,
            type="ARCA",
            tenant=TEST_TENANT,
            creator="test_user",
            modifier="test_user",
            status="CREATED",
            name="List Template",
            description="Template for list test",
            config={"base_url": "test"},
        )

        total, records = repo.list_templates(tenant=TEST_TENANT, page=1, page_size=10)
        assert total >= 1
        uuids = {r.template_uuid for r in records}
        assert template_uuid in uuids
