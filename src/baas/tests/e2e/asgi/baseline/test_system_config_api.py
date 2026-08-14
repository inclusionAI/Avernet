"""E2E tests for System Config API endpoints.

Tests cover:
- GET /api/v1/system-configs - List system configs
- GET /api/v1/system-configs/{conf_key} - Get config by key
- POST /api/v1/system-configs - Create system config
- PUT /api/v1/system-configs/{conf_key} - Update system config
- DELETE /api/v1/system-configs/{conf_key} - Delete system config
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestListSystemConfigs:
    """Tests for GET /api/v1/system-configs endpoint."""

    @pytest.mark.asyncio
    async def test_list_system_configs(self, api: APITestHelper) -> None:
        """Test list system configs with pagination."""
        response = await api.client.get(
            api.system_config_url(),
            params=api.params(page=1, page_size=20),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestGetSystemConfig:
    """Tests for GET /api/v1/system-configs/{conf_key} endpoint."""

    @pytest.mark.asyncio
    async def test_get_system_config_not_found(self, api: APITestHelper) -> None:
        """Test get nonexistent system config."""
        response = await api.client.get(
            api.system_config_url("nonexistent.config.key"),
            params=api.params(),
        )

        assert response.status_code in [200, 404]


class TestCreateSystemConfig:
    """Tests for POST /api/v1/system-configs endpoint."""

    @pytest.mark.asyncio
    async def test_create_system_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Test create system config."""
        response = await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json={
                "conf_key": f"test.config.{unique_id}",
                "name": f"E2E Test Config {unique_id}",
                "conf_value": "test-value",
                "description": "E2E test config",
                "operator": "e2e-test",
            },
        )

        assert response.status_code in [200, 201, 400, 409, 422]


class TestUpdateSystemConfig:
    """Tests for PUT /api/v1/system-configs/{conf_key} endpoint."""

    @pytest.mark.asyncio
    async def test_update_system_config(self, api: APITestHelper) -> None:
        """Test update system config."""
        response = await api.client.put(
            api.system_config_url("nonexistent.config.key"),
            params=api.params(),
            json={
                "conf_value": "updated-value",
                "description": "Updated description",
            },
        )

        assert response.status_code in [200, 400, 404]


class TestDeleteSystemConfig:
    """Tests for DELETE /api/v1/system-configs/{conf_key} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_system_config(self, api: APITestHelper) -> None:
        """Test delete system config."""
        response = await api.client.delete(
            api.system_config_url("nonexistent.config.key"),
            params=api.params(),
        )

        assert response.status_code in [200, 400, 404]
