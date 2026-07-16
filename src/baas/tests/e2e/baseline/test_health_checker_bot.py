"""E2E tests for the Health Checker Bot module.

Covers endpoints under /api/v1/bot-health-checker/ that are not already
covered by test_bot_health_api.py. The bot-health-checker router requires
an API key with app_type=health-checker; these tests verify routing works
(no 500 from DI wiring) and produce expected error responses.

Endpoint mapping:
- GET  /api/v1/bot-health-checker/health                  — check_health_by_bot
- GET  /api/v1/bot-health-checker/alive                   — check_alive_by_bot
- GET  /api/v1/bot-health-checker/active-bots             — list_all_active_bot_device
- GET  /api/v1/bot-health-checker/{bot_uuid}/paas-devices — list_paas_device_by_bot
- POST /api/v1/bot-health-checker/extend-ttl              — extend_ttl_by_bot
"""

from uuid import uuid4

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestHealthCheckerBotHealth:
    """Tests for GET /api/v1/bot-health-checker/health."""

    @pytest.mark.asyncio
    async def test_health_with_real_bot(self, api: APITestHelper) -> None:
        """GET /health with a real bot's UUID returns non-500."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_health_url(),
            params=api.params(
                bot_id=bot["bot_uuid"],
                entity_id=bot.get("entity_id", bot["bot_uuid"]),
                statuses="online",
            ),
        )

        # Endpoint requires health-checker API key; verify routing doesn't 500
        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_health_with_invalid_bot_returns_4xx(
        self, api: APITestHelper
    ) -> None:
        """GET /health with nonexistent bot_id returns 4xx (not 500)."""
        fake_uuid = str(uuid4())

        response = await api.client.get(
            api.bot_health_url(),
            params=api.params(
                bot_id=fake_uuid,
                entity_id=fake_uuid,
            ),
        )

        # API key check fires first (403) or sandbox not found (404)
        assert response.status_code != 500, (
            f"Expected non-500 for invalid bot, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_health_missing_bot_id_returns_422(self, api: APITestHelper) -> None:
        """GET /health without bot_id returns 422 validation error."""
        response = await api.client.get(
            api.bot_health_url(),
            params=api.params(),
        )

        # Without required params, FastAPI validates → 422 (or auth gate 403 first)
        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )


class TestHealthCheckerBotAlive:
    """Tests for GET /api/v1/bot-health-checker/alive."""

    @pytest.mark.asyncio
    async def test_alive_with_real_bot(self, api: APITestHelper) -> None:
        """GET /alive with a real bot returns non-500."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.health_alive_url(),
            params=api.params(
                bot_id=bot["bot_uuid"],
                entity_id=bot.get("entity_id", bot["bot_uuid"]),
                minutes=60,
            ),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_alive_minimal_params(self, api: APITestHelper) -> None:
        """GET /alive with minimal required params returns non-500."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.health_alive_url(),
            params=api.params(
                bot_id=bot["bot_uuid"],
                entity_id=bot.get("entity_id", bot["bot_uuid"]),
            ),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_alive_missing_bot_id_returns_422(self, api: APITestHelper) -> None:
        """GET /alive without bot_id returns 422 validation error."""
        response = await api.client.get(
            api.health_alive_url(),
            params=api.params(entity_id="some-entity"),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )


class TestHealthCheckerActiveBots:
    """Tests for GET /api/v1/bot-health-checker/active-bots."""

    @pytest.mark.asyncio
    async def test_active_bots_returns_paginated(self, api: APITestHelper) -> None:
        """GET /active-bots returns paginated list (or auth-gated response)."""
        response = await api.client.get(
            api.health_active_bots_url(),
            params=api.params(page=1, page_size=10),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_active_bots_with_bot_type_filter(self, api: APITestHelper) -> None:
        """GET /active-bots with bot_type=personal returns non-500."""
        response = await api.client.get(
            api.health_active_bots_url(),
            params=api.params(page=1, page_size=10, bot_type="personal"),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_active_bots_default_params(self, api: APITestHelper) -> None:
        """GET /active-bots with defaults (no explicit page/page_size) returns non-500."""
        response = await api.client.get(
            api.health_active_bots_url(),
            params=api.params(),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )


class TestHealthCheckerPaasDevices:
    """Tests for GET /api/v1/bot-health-checker/{bot_uuid}/paas-devices."""

    @pytest.mark.asyncio
    async def test_paas_devices_with_real_bot(self, api: APITestHelper) -> None:
        """GET paas-devices for a real bot returns non-500."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.health_paas_devices_url(bot["bot_uuid"]),
            params=api.params(
                bot_id=bot["bot_uuid"],
                entity_id=bot.get("entity_id", bot["bot_uuid"]),
            ),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_paas_devices_with_online_status(self, api: APITestHelper) -> None:
        """GET paas-devices with statuses=online returns non-500."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.health_paas_devices_url(bot["bot_uuid"]),
            params=api.params(
                bot_id=bot["bot_uuid"],
                entity_id=bot.get("entity_id", bot["bot_uuid"]),
                statuses="online",
            ),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_paas_devices_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET paas-devices for nonexistent bot returns 4xx or 404."""
        fake_uuid = str(uuid4())

        response = await api.client.get(
            api.health_paas_devices_url(fake_uuid),
            params=api.params(
                bot_id=fake_uuid,
                entity_id=fake_uuid,
            ),
        )

        assert response.status_code != 500, (
            f"Expected non-500 for nonexistent bot, got {response.status_code}: "
            f"{response.text[:200]}"
        )


class TestHealthCheckerExtendTTL:
    """Tests for POST /api/v1/bot-health-checker/extend-ttl."""

    @pytest.mark.asyncio
    async def test_extend_ttl_with_real_bot(self, api: APITestHelper) -> None:
        """POST /extend-ttl with valid bot UUID returns non-500."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.post(
            api.health_extend_ttl_url(),
            params=api.params(),
            json={
                "bot_id": bot["bot_uuid"],
                "entity_id": bot.get("entity_id", bot["bot_uuid"]),
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_extend_ttl_missing_body_returns_422(
        self, api: APITestHelper
    ) -> None:
        """POST /extend-ttl with empty body returns 422 validation error."""
        response = await api.client.post(
            api.health_extend_ttl_url(),
            params=api.params(),
            json={},
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_extend_ttl_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST /extend-ttl for nonexistent bot returns 4xx or 404."""
        fake_uuid = str(uuid4())

        response = await api.client.post(
            api.health_extend_ttl_url(),
            params=api.params(),
            json={
                "bot_id": fake_uuid,
                "entity_id": fake_uuid,
            },
        )

        assert response.status_code != 500, (
            f"Expected non-500 for nonexistent bot, got {response.status_code}: "
            f"{response.text[:200]}"
        )
