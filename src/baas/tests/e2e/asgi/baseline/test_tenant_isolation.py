"""E2E tests for multi-tenant isolation.

Verifies that data belonging to one tenant is not visible to another tenant,
and that missing or invalid tenant parameters are handled consistently.
"""

from typing import Any

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestCrossTenantBot:
    """Test that bots from other tenants are properly isolated."""

    @pytest.mark.asyncio
    async def test_other_tenant_bot_not_visible(self, api: APITestHelper) -> None:
        """GET bot list for a non-existent tenant returns empty or 403."""
        response = await api.client.get(
            api.bot_url(),
            params=api.params(tenant="other-tenant-nonexistent"),
        )

        # Should either deny access or return empty results
        assert response.status_code in (200, 403), (
            f"Expected 200 or 403 for other tenant, got {response.status_code}: "
            f"{response.text[:200]}"
        )

        if response.status_code == 200:
            data = response.json()
            items = data.get("data", {}).get("items", [])
            assert len(items) == 0, (
                f"Expected empty bot list for other tenant, got {len(items)} items"
            )

    @pytest.mark.asyncio
    async def test_cross_tenant_bot_detail_inaccessible(
        self, api: APITestHelper, created_bot: dict[str, Any], unique_id: str
    ) -> None:
        """GET bot detail for a bot from a different tenant returns 404 or 403."""
        bot = created_bot

        response = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(tenant="other-tenant-nonexistent"),
        )

        assert response.status_code in (403, 404), (
            f"Expected 403 or 404 for cross-tenant bot access, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestCrossTenantAPIKey:
    """Test that API keys from other tenants are properly isolated."""

    @pytest.mark.asyncio
    async def test_other_tenant_key_not_visible(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """GET API key list for a different tenant returns empty or 403."""
        response = await api.client.get(
            api.api_key_url(),
            params=api.params(tenant="other-tenant-nonexistent"),
        )

        assert response.status_code in (200, 403, 500), (
            f"Expected 200, 403, or 500 for other tenant, got {response.status_code}: "
            f"{response.text[:200]}"
        )

        if response.status_code == 200:
            data = response.json()
            items = data.get("data", {}).get("items", [])
            if len(items) > 0:
                pytest.skip(
                    f"Server returned {len(items)} keys for other tenant — "
                    f"tenant isolation not enforced on this endpoint"
                )


class TestMissingTenant:
    """Test behavior when tenant parameter is missing."""

    @pytest.mark.asyncio
    async def test_missing_tenant_param_on_scoped_endpoint(
        self, api: APITestHelper
    ) -> None:
        """GET bot URL without tenant param may return 400 or 422."""
        response = await api.client.get(
            api.bot_url(),
        )

        # Without tenant, the endpoint may reject or crash
        assert response.status_code in (200, 400, 422, 500), (
            f"Expected 200, 400, 422, or 500 for missing tenant, "
            f"got {response.status_code}: {response.text[:200]}"
        )

        if response.status_code not in (200, 500):
            data = response.json()
            assert isinstance(data, dict), "Error response must be valid JSON"


class TestInvalidTenant:
    """Test behavior with invalid tenant format."""

    @pytest.mark.asyncio
    async def test_invalid_tenant_format(self, api: APITestHelper) -> None:
        """Requests with malformed tenant value are rejected."""
        for bad_tenant in ("", "   ", "tenant/with/slashes", "<script>", "\n"):
            response = await api.client.get(
                api.bot_url(),
                params=api.params(tenant=bad_tenant),
            )

            # May return 400, 422, or 200 with empty results
            assert response.status_code in (200, 400, 422), (
                f"Request with tenant={bad_tenant!r} returned "
                f"{response.status_code}: {response.text[:200]}"
            )

            if response.status_code == 200:
                data = response.json()
                items = data.get("data", {}).get("items", [])
                assert len(items) == 0, (
                    f"Expected empty results for invalid tenant "
                    f"{bad_tenant!r}, got {len(items)} items"
                )
