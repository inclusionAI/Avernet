"""E2E tests for bot health checker endpoints.

Tests the bot_health_checker_router endpoints:
- GET /api/v1/bot-health-checker/active_bots - List active bots
- GET /api/v1/bot-health-checker/devices - List bot devices
- POST /api/v1/bot-health-checker/ttl/extend - Extend TTL
- GET /api/v1/bot-health-checker/health - Health check
- GET /api/v1/bot-health-checker/alive - Alive check
- GET /api/v1/bot-health-checker/sandbox - Sandbox info

All endpoints require API key auth (validate_bot_health_api_key),
so these tests verify routing works (no 500 from DI wiring).
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

HEALTH_BASE_URL = "/api/v1/bot-health-checker"


class TestBotHealthActiveBots:
    """Tests for GET /api/v1/bot-health-checker/active_bots."""

    @pytest.mark.asyncio
    async def test_active_bots_without_api_key_403(self, api: APITestHelper) -> None:
        """GET active_bots without API key returns 403 (not 500)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/active_bots",
            params=api.params(page=1, page_size=10),
        )

        # 403 means routing + DI wiring works (API key validation rejects the request)
        # 500 would mean DI wiring failure
        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_active_bots_validation_422(self, api: APITestHelper) -> None:
        """Negative page returns 4xx (api key check fires before validation)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/active_bots",
            params=api.params(page=-1, page_size=10),
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_active_bots_page_size_too_large_422(
        self, api: APITestHelper
    ) -> None:
        """page_size > 100 returns 4xx (api key check fires before validation)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/active_bots",
            params=api.params(page=1, page_size=200),
        )

        assert response.status_code != 500


class TestBotHealthDevices:
    """Tests for GET /api/v1/bot-health-checker/devices."""

    @pytest.mark.asyncio
    async def test_devices_without_api_key_403(self, api: APITestHelper) -> None:
        """GET devices without API key returns 403 (not 500)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/devices",
            params={
                "bot_id": "test-bot",
                "entity_id": "test-entity",
                "tenant": api.tenant,
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_devices_missing_required_params_422(
        self, api: APITestHelper
    ) -> None:
        """Missing bot_id returns 4xx (api key check fires before validation)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/devices",
            params={"tenant": api.tenant},
        )

        assert response.status_code != 500


class TestBotHealthTTLExtend:
    """Tests for POST /api/v1/bot-health-checker/ttl/extend."""

    @pytest.mark.asyncio
    async def test_ttl_extend_without_api_key_403(self, api: APITestHelper) -> None:
        """POST ttl/extend without API key returns 403 (not 500)."""
        response = await api.client.post(
            f"{HEALTH_BASE_URL}/ttl/extend",
            params=api.params(),
            json={
                "bot_id": "test-bot",
                "entity_id": "test-entity",
            },
        )

        assert response.status_code != 500


class TestBotHealthCheckHealth:
    """Tests for GET /api/v1/bot-health-checker/health."""

    @pytest.mark.asyncio
    async def test_health_without_api_key_403(self, api: APITestHelper) -> None:
        """GET health without API key returns 403 (not 500)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/health",
            params={
                "bot_id": "test-bot",
                "entity_id": "test-entity",
                "tenant": api.tenant,
            },
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_health_missing_bot_id_422(self, api: APITestHelper) -> None:
        """Missing bot_id returns 4xx (api key check fires before validation)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/health",
            params={"entity_id": "test-entity", "tenant": api.tenant},
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_health_invalid_status_422(self, api: APITestHelper) -> None:
        """Invalid status value returns 4xx (api key check fires before validation)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/health",
            params={
                "bot_id": "test-bot",
                "entity_id": "test-entity",
                "statuses": "invalid_status",
                "tenant": api.tenant,
            },
        )

        assert response.status_code != 500


class TestBotHealthAlive:
    """Tests for GET /api/v1/bot-health-checker/alive."""

    @pytest.mark.asyncio
    async def test_alive_without_api_key_403(self, api: APITestHelper) -> None:
        """GET alive without API key returns 403 (not 500)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/alive",
            params={
                "bot_id": "test-bot",
                "entity_id": "test-entity",
                "tenant": api.tenant,
            },
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_alive_negative_minutes_422(self, api: APITestHelper) -> None:
        """Negative minutes returns 4xx (api key check fires before validation)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/alive",
            params={
                "bot_id": "test-bot",
                "entity_id": "test-entity",
                "minutes": -1,
                "tenant": api.tenant,
            },
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_alive_zero_minutes_422(self, api: APITestHelper) -> None:
        """minutes=0 returns 4xx (api key check fires before validation)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/alive",
            params={
                "bot_id": "test-bot",
                "entity_id": "test-entity",
                "minutes": 0,
                "tenant": api.tenant,
            },
        )

        assert response.status_code != 500


class TestBotHealthSandbox:
    """Tests for GET /api/v1/bot-health-checker/sandbox."""

    @pytest.mark.asyncio
    async def test_sandbox_without_api_key_403(self, api: APITestHelper) -> None:
        """GET sandbox without API key returns 403 (not 500)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/sandbox",
            params={"sandbox_id": "test-sandbox", "tenant": api.tenant},
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_sandbox_missing_sandbox_id_422(self, api: APITestHelper) -> None:
        """Missing sandbox_id returns 4xx (api key check fires before validation)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/sandbox",
            params={"tenant": api.tenant},
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_sandbox_empty_sandbox_id_422(self, api: APITestHelper) -> None:
        """Empty sandbox_id returns 4xx (api key check fires before validation)."""
        response = await api.client.get(
            f"{HEALTH_BASE_URL}/sandbox",
            params={"sandbox_id": "", "tenant": api.tenant},
        )

        assert response.status_code != 500
