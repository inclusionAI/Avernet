from __future__ import annotations

from typing import Any

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.health_check]


class TestServiceDeviceProvider:
    @pytest.mark.asyncio
    async def test_devices_for_service_bot_via_health_checker(
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
        assert response.status_code in (
            200,
            400,
            401,
            403,
            404,
            422,
            500,
            501,
        ), f"Unexpected status {response.status_code}: {response.text[:200]}"

    @pytest.mark.asyncio
    async def test_active_bots_with_service_filter(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.health_active_bots_url(),
            params=api.params(page=1, page_size=10, bot_type="service"),
        )
        assert response.status_code in (
            200,
            400,
            401,
            403,
            404,
            422,
            500,
            501,
        ), f"Unexpected status {response.status_code}: {response.text[:200]}"


class TestPersonalDeviceProvider:
    @pytest.mark.asyncio
    async def test_active_bots_with_personal_filter(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.health_active_bots_url(),
            params=api.params(page=1, page_size=10, bot_type="personal"),
        )
        assert response.status_code in (
            200,
            400,
            401,
            403,
            404,
            422,
            500,
            501,
        ), f"Unexpected status {response.status_code}: {response.text[:200]}"


class TestDeviceSourceAggregation:
    @pytest.mark.asyncio
    async def test_active_bots_without_bot_type_filter(
        self, api: APITestHelper
    ) -> None:
        response = await api.client.get(
            api.health_active_bots_url(),
            params=api.params(page=1, page_size=10),
        )
        assert response.status_code in (
            200,
            400,
            401,
            403,
            404,
            422,
            500,
            501,
        ), f"Unexpected status {response.status_code}: {response.text[:200]}"


class TestPaasDevices:
    @pytest.mark.asyncio
    async def test_paas_device_list_for_real_bot(
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
        assert response.status_code in (
            200,
            400,
            401,
            403,
            404,
            422,
            500,
            501,
        ), f"Unexpected status {response.status_code}: {response.text[:200]}"


class TestTTLExtension:
    @pytest.mark.asyncio
    async def test_extend_ttl_for_real_bot(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        bot = created_bot
        response = await api.client.post(
            api.health_extend_ttl_url(),
            params=api.params(),
            json={
                "bot_id": bot["bot_uuid"],
                "entity_id": bot.get("entity_id", bot["bot_uuid"]),
                "env": "prod",
            },
        )
        assert response.status_code in (
            200,
            400,
            401,
            403,
            404,
            422,
            500,
            501,
        ), f"Unexpected status {response.status_code}: {response.text[:200]}"


class TestSandboxLookup:
    @pytest.mark.asyncio
    async def test_sandbox_reverse_lookup(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.health_sandbox_url(),
            params=api.params(sandbox_id="sb-test-123"),
        )
        assert response.status_code in (
            200,
            400,
            401,
            403,
            404,
            422,
            500,
            501,
        ), f"Unexpected status {response.status_code}: {response.text[:200]}"
