from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.tenant import (
    TenantRecord,
    TenantRepository,
)
from secbaas.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


class TestTenantSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: TenantRepository = get_container().repository.tenant_repository()
        tenant_name = f"t_sqlite_{_generate_uuid()[:8]}"
        extra = {"region": "cn"}

        tenant_id = repo.insert_tenant(
            creator="u",
            modifier="u",
            name=tenant_name,
            description="Sqlite Tenant",
            env=TEST_ENV,
            extra_config=extra,
        )
        assert tenant_id > 0

        record = repo.get_by_id(tenant_id)
        assert isinstance(record, TenantRecord)
        assert record.name == tenant_name
        assert record.env == TEST_ENV
        assert record.extra_config == extra
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.tenant_repository()
        assert repo.get_by_id(99999999) is None

    def test_deep_null_preservation(self):
        repo = get_container().repository.tenant_repository()
        tenant_name = f"t_null_{_generate_uuid()[:8]}"

        repo.insert_tenant(
            creator="u",
            modifier="u",
            name=tenant_name,
            description=None,
            env=TEST_ENV,
            extra_config={},
        )
        record = repo.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.description is None

    def test_deep_json_roundtrip(self):
        repo = get_container().repository.tenant_repository()
        tenant_name = f"t_json_{_generate_uuid()[:8]}"
        extra = {"nested": {"key": "value"}, "list": [1, "two", None]}

        repo.insert_tenant(
            creator="u",
            modifier="u",
            name=tenant_name,
            description="JSON Tenant",
            env=TEST_ENV,
            extra_config=extra,
        )
        record = repo.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.extra_config == extra

    def test_deep_update_tenant(self):
        repo = get_container().repository.tenant_repository()
        tenant_name = f"t_upd_{_generate_uuid()[:8]}"

        repo.insert_tenant(
            creator="orig",
            modifier="orig",
            name=tenant_name,
            description="Original",
            env=TEST_ENV,
            extra_config={},
        )
        repo.update_tenant(
            name=tenant_name,
            env=TEST_ENV,
            modifier="updater",
            description="Updated",
        )
        record = repo.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.description == "Updated"
        assert record.name == tenant_name
        assert record.creator == "orig"

    def test_insert_tenant_and_get_by_id(self):
        repo: TenantRepository = get_container().repository.tenant_repository()
        tenant_name = f"equiv_tenant_{_generate_uuid()[:12]}"

        tenant_id = repo.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="Equivalence test tenant",
            env=TEST_ENV,
            extra_config={},
        )
        assert tenant_id > 0

        record = repo.get_by_id(tenant_id)
        assert isinstance(record, TenantRecord)
        assert record.id == tenant_id
        assert record.name == tenant_name
        assert record.env == TEST_ENV
        assert record.description == "Equivalence test tenant"
        assert record.creator == "test_user"
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_name(self):
        repo = get_container().repository.tenant_repository()
        tenant_name = f"equiv_tenant_{_generate_uuid()[:12]}"

        repo.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="Get by name test",
            env=TEST_ENV,
            extra_config={},
        )

        record = repo.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.name == tenant_name

    def test_get_by_name_nonexistent(self):
        repo = get_container().repository.tenant_repository()
        assert repo.get_by_name(f"nonexistent_{_generate_uuid()}", TEST_ENV) is None

    def test_update_tenant(self):
        repo = get_container().repository.tenant_repository()
        tenant_name = f"equiv_tenant_{_generate_uuid()[:12]}"

        repo.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="Original",
            env=TEST_ENV,
            extra_config={},
        )
        repo.update_tenant(
            name=tenant_name,
            env=TEST_ENV,
            modifier="admin",
            description="Updated description",
        )

        record = repo.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.description == "Updated description"

    def test_soft_delete(self):
        repo = get_container().repository.tenant_repository()
        tenant_name = f"equiv_tenant_{_generate_uuid()[:12]}"

        tenant_id = repo.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="To delete",
            env=TEST_ENV,
            extra_config={},
        )
        repo.soft_delete(name=tenant_name, env=TEST_ENV, modifier="admin")

        result = repo.get_by_name(tenant_name, TEST_ENV)
        assert result is None

    def test_list_tenants(self):
        repo = get_container().repository.tenant_repository()
        tenant_name = f"equiv_tenant_{_generate_uuid()[:12]}"

        repo.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="List test",
            env=TEST_ENV,
            extra_config={},
        )

        total, records = repo.list_tenants(env=TEST_ENV, page=1, page_size=10)
        assert total >= 1
        names = {r.name for r in records}
        assert tenant_name in names
