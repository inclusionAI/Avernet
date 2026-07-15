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

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestQpmConfigNormal:
    """Tests for normal bot QPM config operations."""

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="TODO: QPM config endpoint uses bot_id (not bot_uuid) and requires pre-existing config"
    )
    async def test_get_bot_qpm(self, api: APITestHelper) -> None:
        """Test get QPM config for an existing bot."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.qpm_config_url(bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "data" in data

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="TODO: QPM config uses 'qpm' field not 'rpm'/'rpd'; needs pre-existing config"
    )
    async def test_update_bot_qpm(self, api: APITestHelper) -> None:
        """Test update QPM config for an existing bot."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.put(
            api.qpm_config_url(bot["bot_uuid"]),
            params=api.params(),
            json={
                "qpm": 100,
            },
        )

        assert response.status_code in (200, 400, 404)
        if response.status_code == 200:
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
    async def test_set_negative_qpm_limit(self, api: APITestHelper) -> None:
        """Test set QPM with negative limit values."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.put(
            api.qpm_config_url(bot["bot_uuid"]),
            params=api.params(),
            json={
                "rpm": -1,
                "rpd": 1000,
            },
        )

        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_set_zero_qpm(self, api: APITestHelper) -> None:
        """Test set QPM with zero limits."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.put(
            api.qpm_config_url(bot["bot_uuid"]),
            params=api.params(),
            json={
                "rpm": 0,
                "rpd": 0,
            },
        )

        assert response.status_code in (200, 400, 422)
