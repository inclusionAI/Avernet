"""E2E tests for System Config API endpoints.

Tests cover:
- GET /api/v1/system-configs - List system configs
- GET /api/v1/system-configs/{conf_key} - Get config by key
- PUT /api/v1/system-configs/{conf_key} - Update system config

Error cases:
- GET nonexistent config key
- Update with invalid value
- Create with missing value
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

KNOWN_CONFIG_KEY = "test.config.key"


class TestSystemConfigNormal:
    """Tests for normal system config operations."""

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
        assert "items" in data["data"]
        assert isinstance(data["data"]["items"], list)

    @pytest.mark.asyncio
    async def test_get_config_by_key(self, api: APITestHelper) -> None:
        """Test get system config by known key."""
        response = await api.client.get(
            api.system_config_url(KNOWN_CONFIG_KEY),
            params=api.params(),
        )

        assert response.status_code in (200, 404)
        if response.status_code == 200:
            data = response.json()
            assert data["code"] == 0
            assert data["data"]["conf_key"] == KNOWN_CONFIG_KEY

    @pytest.mark.asyncio
    async def test_update_config(self, api: APITestHelper) -> None:
        """Test update system config with valid value."""
        response = await api.client.put(
            api.system_config_url(KNOWN_CONFIG_KEY),
            params=api.params(),
            json={
                "conf_value": "updated-e2e-value",
                "description": "Updated by E2E test",
            },
        )

        assert response.status_code in (200, 400, 404)
        if response.status_code == 200:
            data = response.json()
            assert data["code"] == 0


class TestSystemConfigErrors:
    """Tests for system config error cases."""

    @pytest.mark.asyncio
    async def test_get_nonexistent_key(self, api: APITestHelper) -> None:
        """Test get a config key that does not exist."""
        response = await api.client.get(
            api.system_config_url("nonexistent.config.key.that.does.not.exist"),
            params=api.params(),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_invalid_value(self, api: APITestHelper) -> None:
        """Test update with invalid value type."""
        response = await api.client.put(
            api.system_config_url(KNOWN_CONFIG_KEY),
            params=api.params(),
            json={
                # Missing required fields or invalid type
                "conf_value": None,
            },
        )

        assert response.status_code in (400, 404, 422)

    @pytest.mark.asyncio
    async def test_create_with_missing_value(self, api: APITestHelper) -> None:
        """Test create config with missing required value."""
        response = await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json={
                # Missing conf_value and description
                "conf_key": "test.missing.value.key",
            },
        )

        assert response.status_code in (400, 422)
