"""E2E tests for Bot QPM Config API endpoints.

Tests cover:
- GET /api/v1/bots/{uuid}/qpm - Get bot QPM config
- PUT /api/v1/bots/{uuid}/qpm - Update bot QPM config

Error cases:
- GET QPM for nonexistent bot UUID
- Set negative QPM limit
- Set zero QPM
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestQpmConfigNormal:
    """Tests for normal bot QPM config operations."""

    @pytest.mark.asyncio
    async def test_get_bot_qpm(self, api: APITestHelper, created_bot: dict) -> None:
        """Test get QPM config for an existing bot."""
        bot_id = str(created_bot["id"])

        create_resp = await api.client.post(
            api.qpm_config_url(),
            params=api.params(),
            json={"bot_id": bot_id, "qpm": 60},
        )
        assert create_resp.status_code in (200, 201)

        response = await api.client.get(
            api.qpm_config_url(bot_id),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "data" in data

    @pytest.mark.asyncio
    async def test_update_bot_qpm(self, api: APITestHelper, created_bot: dict) -> None:
        """Test update QPM config for an existing bot."""
        bot_id = str(created_bot["id"])

        create_resp = await api.client.post(
            api.qpm_config_url(),
            params=api.params(),
            json={"bot_id": bot_id, "qpm": 60},
        )
        assert create_resp.status_code in (200, 201)

        response = await api.client.put(
            api.qpm_config_url(bot_id),
            params=api.params(),
            json={
                "qpm": 100,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestQpmConfigErrors:
    """Tests for bot QPM config error cases."""

    @pytest.mark.asyncio
    async def test_get_qpm_nonexistent_bot(self, api: APITestHelper) -> None:
        """Test get QPM config for a nonexistent bot UUID."""
        response = await api.client.get(
            api.qpm_config_url("nonexistent-bot-uuid-00000"),
            params=api.params(),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_set_negative_qpm_limit(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """Test set QPM with negative limit values."""
        response = await api.client.put(
            api.qpm_config_url(created_bot["bot_uuid"]),
            params=api.params(),
            json={
                "rpm": -1,
                "rpd": 1000,
            },
        )

        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_set_zero_qpm(self, api: APITestHelper, created_bot: dict) -> None:
        """Test set QPM with zero limits."""
        response = await api.client.put(
            api.qpm_config_url(created_bot["bot_uuid"]),
            params=api.params(),
            json={
                "rpm": 0,
                "rpd": 0,
            },
        )

        assert response.status_code in (200, 400, 422)
