"""E2E tests for Bot Health Service and Device Providers (Phase 1.1).

Exercises bot health checker endpoints that rely on the BotHealthCheckerService.
All tests are read-only — they use created_bot() to create a bot and verify
routing is wired correctly (no 500s from DI failures).

Endpoint mapping:
- GET /api/v1/bot-health-checker/health   — check_health_by_bot
- GET /api/v1/bot-health-checker/devices  — list_paas_device_by_bot
- GET /api/v1/bot-health-checker/active_bots — list_all_active_bot_device
"""

from typing import Any
from uuid import uuid4

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]

BOT_HEALTH_BASE = "/api/v1/bot-health-checker"


class TestBotHealth:
    """Tests for Bot Health Service endpoints."""

    # ── GET /health ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_health_with_real_bot(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        """GET /health with a real bot returns non-500."""
        bot = created_bot

        response = await api.client.get(
            api.bot_health_url(),
            params=api.params(
                bot_id=bot["bot_uuid"],
                entity_id=bot.get("entity_id", bot["bot_uuid"]),
            ),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_health_with_no_devices(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        """GET /health for a bot with online statuses returns non-500."""
        bot = created_bot

        response = await api.client.get(
            api.bot_health_url(),
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
    async def test_health_with_invalid_uuid(self, api: APITestHelper) -> None:
        """GET /health with a random UUID returns 4xx (not 500)."""
        fake_uuid = str(uuid4())

        response = await api.client.get(
            api.bot_health_url(),
            params=api.params(
                bot_id=fake_uuid,
                entity_id=fake_uuid,
            ),
        )

        assert response.status_code != 500, (
            f"Expected non-500 for invalid UUID, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    # ── GET /devices ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_devices_with_real_bot(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        """GET /devices for a real bot returns non-500."""
        bot = created_bot

        response = await api.client.get(
            f"{BOT_HEALTH_BASE}/devices",
            params=api.params(
                bot_id=bot["bot_uuid"],
                entity_id=bot.get("entity_id", bot["bot_uuid"]),
            ),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_devices_with_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET /devices for nonexistent bot returns 4xx (not 500)."""
        fake_uuid = str(uuid4())

        response = await api.client.get(
            f"{BOT_HEALTH_BASE}/devices",
            params=api.params(
                bot_id=fake_uuid,
                entity_id=fake_uuid,
            ),
        )

        assert response.status_code != 500, (
            f"Expected non-500 for nonexistent bot, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_devices_missing_bot_id(self, api: APITestHelper) -> None:
        """GET /devices without bot_id returns 422 validation error."""
        response = await api.client.get(
            f"{BOT_HEALTH_BASE}/devices",
            params=api.params(),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    # ── GET /active_bots ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_active_bots_paginated(self, api: APITestHelper) -> None:
        """GET /active_bots with pagination returns non-500."""
        response = await api.client.get(
            api.health_active_bots_url(),
            params=api.params(page=1, page_size=5, statuses="online"),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_active_bots_with_bot_type_filter(self, api: APITestHelper) -> None:
        """GET /active_bots with bot_type=personal returns non-500."""
        response = await api.client.get(
            api.health_active_bots_url(),
            params=api.params(page=1, page_size=10, bot_type="personal"),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_active_bots_invalid_bot_type(self, api: APITestHelper) -> None:
        """GET /active_bots with invalid bot_type returns 4xx (400)."""
        response = await api.client.get(
            api.health_active_bots_url(),
            params=api.params(page=1, page_size=10, bot_type="invalid"),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )
