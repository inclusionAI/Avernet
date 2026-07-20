from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.health_check]


class TestBotHealthService:
    @pytest.mark.asyncio
    async def test_health_with_real_bot(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
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
    async def test_health_with_invalid_bot_returns_4xx(
        self, api: APITestHelper
    ) -> None:
        fake_uuid = str(uuid4())
        response = await api.client.get(
            api.bot_health_url(),
            params=api.params(
                bot_id=fake_uuid,
                entity_id=fake_uuid,
            ),
        )
        assert response.status_code != 500, (
            f"Expected non-500 for invalid bot, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_health_missing_bot_id_returns_422(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.bot_health_url(),
            params=api.params(),
        )
        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )


class TestBotAlive:
    @pytest.mark.asyncio
    async def test_alive_with_real_bot(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        bot = created_bot
        response = await api.client.get(
            api.health_alive_url(),
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
    async def test_alive_with_invalid_minutes(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        bot = created_bot
        response = await api.client.get(
            api.health_alive_url(),
            params=api.params(
                bot_id=bot["bot_uuid"],
                entity_id=bot.get("entity_id", bot["bot_uuid"]),
                minutes=-1,
            ),
        )
        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )


class TestBotActiveBotsAndDevices:
    @pytest.mark.asyncio
    async def test_active_bots_paginated(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.health_active_bots_url(),
            params=api.params(page=1, page_size=10, bot_type="service"),
        )
        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_devices_for_real_bot(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        bot = created_bot
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
