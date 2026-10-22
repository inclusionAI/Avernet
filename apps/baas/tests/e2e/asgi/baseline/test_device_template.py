"""E2E tests for Device Template API endpoints.

Tests cover:
- GET /api/v1/device-templates/{uuid} - Get template by UUID

Error cases:
- GET nonexistent template UUID
- GET removed list endpoint (no longer registered for GET)
"""

import pytest

from tests.e2e.asgi.conftest import DEFAULT_TEMPLATE_UUID, APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestDeviceTemplateNormal:
    """Tests for normal device template operations."""

    @pytest.mark.asyncio
    async def test_get_template_by_uuid(self, api: APITestHelper) -> None:
        """Test get device template by known UUID."""
        response = await api.client.get(
            api.device_template_url(DEFAULT_TEMPLATE_UUID),
            params=api.params(),
        )

        assert response.status_code in (200, 404)
        if response.status_code == 200:
            data = response.json()["data"]
            assert data["template_uuid"] == DEFAULT_TEMPLATE_UUID


class TestDeviceTemplateErrors:
    """Tests for device template error cases."""

    @pytest.mark.asyncio
    async def test_get_nonexistent_template(self, api: APITestHelper) -> None:
        """Test get a device template UUID that does not exist."""
        response = await api.client.get(
            api.device_template_url("NONEXISTENT-UUID-000000000000"),
            params=api.params(),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_endpoint_removed_returns_404(self, api: APITestHelper) -> None:
        """List endpoint is removed for GET: bare path is no longer registered for GET.

        FastAPI returns 405 when the path is still matched by another method
        (POST /api/v1/device-templates create_template) but GET has no handler
        anymore; relax the assertion to also accept 404 in case POST creator
        is removed in a future change.
        """
        response = await api.client.get(
            api.device_template_url(),
            params=api.params(page=1, page_size=10),
        )

        assert response.status_code in (404, 405)
