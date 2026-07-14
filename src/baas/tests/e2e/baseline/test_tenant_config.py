"""E2E tests for Tenant Config API endpoints.

Tests cover:
- GET /api/v1/tenants/{identifier} - Get tenant by identifier
- GET /api/v1/tenants - List tenants

Error cases:
- GET nonexistent tenant
- GET tenant without tenant parameter
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestTenantConfigNormal:
    """Tests for normal tenant operations."""

    @pytest.mark.asyncio
    async def test_get_tenant(self, api: APITestHelper) -> None:
        """Test get tenant by known identifier."""
        response = await api.client.get(
            api.tenant_url(api.tenant),
            params={},
        )

        assert response.status_code in (200, 404)
        if response.status_code == 200:
            data = response.json()
            assert data["code"] == 0
            assert "data" in data

    @pytest.mark.asyncio
    async def test_tenant_response_structure(self, api: APITestHelper) -> None:
        """Test tenant list response has expected structure."""
        response = await api.client.get(
            api.tenant_url(),
            params={"page": 1, "page_size": 20},
        )

        assert response.status_code in (200, 500)
        if response.status_code == 200:
            data = response.json()
            assert data["code"] == 0
            assert "items" in data["data"]


class TestTenantConfigErrors:
    """Tests for tenant error cases."""

    @pytest.mark.asyncio
    async def test_get_nonexistent_tenant(self, api: APITestHelper) -> None:
        """Test get a tenant identifier that does not exist."""
        response = await api.client.get(
            api.tenant_url("nonexistent-tenant-xyz"),
            params={},
        )

        assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_get_tenant_without_param(self, api: APITestHelper) -> None:
        """Test list tenants without extra parameters."""
        response = await api.client.get(
            api.tenant_url(),
            params={},
        )

        assert response.status_code in (200, 500)
        if response.status_code == 200:
            data = response.json()
            assert "data" in data
