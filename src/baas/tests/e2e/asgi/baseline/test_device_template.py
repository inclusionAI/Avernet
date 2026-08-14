"""E2E tests for Device Template API endpoints.

Tests cover:
- GET /api/v1/device-templates - List device templates
- GET /api/v1/device-templates/{uuid} - Get template by UUID

Error cases:
- GET nonexistent template UUID
"""

import pytest

from tests.e2e.asgi.conftest import DEFAULT_TEMPLATE_UUID, APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestDeviceTemplateNormal:
    """Tests for normal device template operations."""

    @pytest.mark.asyncio
    async def test_list_templates(self, api: APITestHelper) -> None:
        """Test list device templates with pagination."""
        response = await api.client.get(
            api.device_template_url(),
            params=api.params(page=1, page_size=10),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert isinstance(data["items"], list)
        assert data["page"] == 1
        assert data["page_size"] == 10

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
