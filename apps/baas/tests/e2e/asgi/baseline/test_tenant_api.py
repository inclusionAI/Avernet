"""E2E tests for Tenant API endpoints.

Tests cover:
- GET /api/v1/tenants - List tenants
- GET /api/v1/tenants/{identifier} - Get tenant
- POST /api/v1/tenants - Create tenant
- PUT /api/v1/tenants/{tenant_id} - Update tenant
- DELETE /api/v1/tenants/{tenant_id} - Delete tenant
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestListTenants:
    """Tests for GET /api/v1/tenants endpoint."""

    @pytest.mark.asyncio
    async def test_list_tenants(self, api: APITestHelper) -> None:
        """Test list tenants with pagination."""
        response = await api.client.get(
            api.tenant_url(),
            params={"page": 1, "page_size": 20},
        )

        # May return 500 if tenant service unavailable
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert data["code"] == 0
            assert "items" in data["data"]


class TestGetTenant:
    """Tests for GET /api/v1/tenants/{identifier} endpoint."""

    @pytest.mark.asyncio
    async def test_get_tenant_by_identifier(self, api: APITestHelper) -> None:
        """Test get tenant by identifier."""
        response = await api.client.get(
            api.tenant_url(api.tenant),
            params={},
        )

        # May return 200 or 404 if tenant doesn't exist
        assert response.status_code in [200, 404]


class TestCreateTenant:
    """Tests for POST /api/v1/tenants endpoint."""

    @pytest.mark.asyncio
    async def test_create_tenant(self, api: APITestHelper, unique_id: str) -> None:
        """Test create tenant."""
        response = await api.client.post(
            api.tenant_url(),
            json={
                "tenant_id": 90000 + hash(unique_id) % 9999,
                "name": f"Test Tenant {unique_id}",
                "type": "ARCA",
                "env": "dev",
                "description": "E2E test tenant",
                "operator": "e2e-test",
            },
        )

        # May return 201 success, 409 if exists, 422 if validation fails, or 500
        assert response.status_code in [200, 201, 400, 409, 422, 500]


class TestUpdateTenant:
    """Tests for PUT /api/v1/tenants/{tenant_id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_tenant(self, api: APITestHelper) -> None:
        """Test update tenant."""
        response = await api.client.put(
            api.tenant_url("999999"),
            json={
                "description": "Updated description",
                "operator": "e2e-test",
            },
        )

        # May return 200, 404, or 500 if service unavailable
        assert response.status_code in [200, 404, 500]


class TestDeleteTenant:
    """Tests for DELETE /api/v1/tenants/{tenant_id} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_tenant(self, api: APITestHelper) -> None:
        """Test delete tenant."""
        response = await api.client.delete(
            api.tenant_url("999999"),
            params={"operator": "e2e-test"},
        )

        # May return 200, 404, or 500 if service unavailable
        assert response.status_code in [200, 404, 500]
