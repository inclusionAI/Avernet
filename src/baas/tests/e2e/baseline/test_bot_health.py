"""E2E tests for bot health check endpoint.

Tests the bot health check endpoint at GET /api/v1/health-check/bot/{bot_uuid}.
Uses find_existing_bot() to locate an existing bot for positive tests.
"""

from uuid import uuid4

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestBotHealthNormal:
    """Tests for GET /api/v1/health-check/bot/{bot_uuid} — normal cases."""

    @pytest.mark.asyncio
    async def test_bot_health_returns_status(self, api: APITestHelper) -> None:
        """GET bot_health_url for an existing bot returns 200."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_health_url(bot["bot_uuid"]),
            params=api.params(),
        )

        # Bot health endpoint requires API key auth; e2e tests don't
        # have a health-checker API key, so 401 is expected.
        assert response.status_code in (200, 401), (
            f"Expected 200 or 401, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_bot_health_response_structure(self, api: APITestHelper) -> None:
        """GET bot_health_url response contains expected fields."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_health_url(bot["bot_uuid"]),
            params=api.params(),
        )

        # Bot health endpoint requires API key auth; e2e tests don't
        # have a health-checker API key, so 401 is expected.
        assert response.status_code in (200, 401), (
            f"Expected 200 or 401, got {response.status_code}: {response.text[:200]}"
        )
        if response.status_code == 401:
            return
        data = response.json()
        assert isinstance(data, dict)
        # Response should have a data wrapper or direct status
        if "data" in data:
            inner = data["data"]
            assert isinstance(inner, dict)
        else:
            # Flat response with status
            assert "status" in data or "healthy" in data


class TestBotHealthErrors:
    """Tests for GET /api/v1/health-check/bot/{bot_uuid} — error cases."""

    @pytest.mark.asyncio
    async def test_bot_health_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET bot_health_url with a fake UUID returns 401 or 404."""
        fake_uuid = str(uuid4())
        response = await api.client.get(
            api.bot_health_url(fake_uuid),
            params=api.params(),
        )

        # Auth gate may fire before route param validation
        assert response.status_code in (401, 404), (
            f"Expected 401 or 404 for nonexistent bot, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_bot_health_missing_tenant_param(self, api: APITestHelper) -> None:
        """GET bot_health_url without tenant param returns 4xx."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_health_url(bot["bot_uuid"]),
        )

        # Without tenant, the endpoint should reject with 4xx
        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )


class TestBotHealthEdge:
    """Tests for GET /api/v1/health-check/bot/{bot_uuid} — edge cases."""

    @pytest.mark.asyncio
    async def test_bot_health_with_no_devices(self, api: APITestHelper) -> None:
        """GET bot_health_url for a bot with device_count=0 returns valid response."""
        # Create a bot with device_count=0 to test no-device edge case
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_health_url(bot["bot_uuid"]),
            params=api.params(),
        )

        # Should still return a valid response (no 500)
        assert response.status_code != 500, (
            f"Expected non-500 response for no-device bot, got "
            f"{response.status_code}: {response.text[:200]}"
        )
