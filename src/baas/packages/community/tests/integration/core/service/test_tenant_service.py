"""Optimized integration tests for DefaultTenantManageService with shared fixtures.

Consolidated tests into comprehensive test methods.
Uses shared fixtures to minimize database record creation.
"""

import time

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.utils.env_utils import get_current_env

TEST_ENV = get_current_env()


def _ts():
    return get_container().services.tenant_service()


def generate_tenant_name() -> str:
    return f"test_tenant_{int(time.time() * 1000000) % 10000000000}"


@pytest.mark.integration
class TestTenantManageServiceIntegration:
    """Optimized integration tests for DefaultTenantManageService."""

    def test_tenant_crud_operations(self, tenant_repository, created_tenant_ids):
        """Consolidated test for tenant CRUD operations.

        Tests:
        - create tenant with valid data
        - get tenant by name found
        - get tenant by name not found
        - update tenant description
        - update multiple fields
        - update nonexistent name returns None
        - list tenants with pagination
        """
        from secbaas.api.tenant_manage import TenantCreate

        tenant_name = generate_tenant_name()

        # Test 1: Create tenant with valid data
        create_data = TenantCreate(
            name=tenant_name,
            description=None,
            extra_config=None,
            operator="test_user",
        )
        result = _ts().create_tenant(create_data)
        assert result is not None
        assert result.name == tenant_name

        # Track for cleanup - get the record to find its id
        repo = tenant_repository
        record = repo.get_by_name(tenant_name, TEST_ENV)
        if record:
            created_tenant_ids.append(record.id)

        # Test 2: Get by name found
        found = _ts().get_tenant_by_name(tenant_name)
        assert found is not None
        assert found.name == tenant_name

        # Test 3: Get by name not found
        not_found = _ts().get_tenant_by_name("nonexistent_tenant")
        assert not_found is None

        # Test 4: Update description
        from secbaas.api.tenant_manage import TenantConfig, TenantUpdate

        update_result = _ts().update_tenant(
            tenant_name,
            TenantUpdate(
                description="Updated desc", extra_config=None, operator="test_user"
            ),
        )
        assert update_result is not None
        assert update_result.description == "Updated desc"

        # Test 5: Update multiple fields
        update_result2 = _ts().update_tenant(
            tenant_name,
            TenantUpdate(
                description="Multi update",
                extra_config=TenantConfig(default_template_uuid="test-uuid"),
                operator="test_user",
            ),
        )
        assert update_result2 is not None
        assert update_result2.description == "Multi update"
        assert update_result2.extra_config is not None

        # Test 6: Update nonexistent name returns None
        nonexistent_name = f"nonexistent_{int(time.time() * 1000)}"
        update_result3 = _ts().update_tenant(
            nonexistent_name,
            TenantUpdate(description="test", extra_config=None, operator="test_user"),
        )
        assert update_result3 is None

        # Test 7: List tenants with pagination
        list_result = _ts().list_tenants(page=1, page_size=10)
        assert list_result.total >= 1
        assert list_result.page == 1

    def test_tenant_soft_delete_operations(self, tenant_repository, created_tenant_ids):
        """Consolidated test for tenant soft delete operations.

        Tests:
        - soft delete tenant
        - soft delete nonexistent tenant
        - list excludes soft deleted
        """
        from secbaas.api.tenant_manage import TenantCreate

        tenant_name = generate_tenant_name()

        # Create tenant
        create_data = TenantCreate(
            name=tenant_name,
            description=None,
            extra_config=None,
            operator="test_user",
        )
        _ts().create_tenant(create_data)

        # Track for cleanup - get the record to find its id
        repo = tenant_repository
        record = repo.get_by_name(tenant_name, TEST_ENV)
        if record:
            created_tenant_ids.append(record.id)

        # Test 1: Soft delete tenant
        delete_result = _ts().soft_delete_tenant(tenant_name, operator="test_user")
        assert delete_result is True

        # Test 2: List excludes soft deleted
        list_result = _ts().list_tenants(page=1, page_size=100)
        # Soft deleted tenant should not appear in the list
        assert not any(t.name == tenant_name for t in list_result.items)

        # Test 3: Soft delete nonexistent tenant returns False
        result = _ts().soft_delete_tenant(
            "nonexistent_tenant_xyz", operator="test_user"
        )
        assert result is False
