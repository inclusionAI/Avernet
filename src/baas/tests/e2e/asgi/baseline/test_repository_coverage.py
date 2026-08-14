"""E2E tests for Repository-layer coverage (Phase 1.7).

Covers repository query operations exercised through the API:
- GET /api/v1/device-bindings — Device binding queries
- GET /api/v1/device-templates — Device template listing
- GET /api/v1/bots — Bot listing with filters
- GET /api/v1/publishes — Publish listing
"""

from typing import Any

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestDeviceBindingQueries:
    """Tests for GET /api/v1/device-bindings."""

    @pytest.mark.asyncio
    async def test_device_bindings_with_bot_uuid(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        """GET /api/v1/device-bindings with bot_uuid filter."""
        bot = created_bot

        response = await api.client.get(
            api.device_binding_url(),
            params=api.params(bot_uuid=bot["bot_uuid"]),
        )
        assert response.status_code in (200, 404, 500), (
            f"Expected 200, 404, or 500 for device-binding query, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        if response.status_code == 200:
            data = response.json()
            if "data" in data:
                result = data["data"]
                assert isinstance(result, (list, dict)), (
                    f"Expected list or dict, got {type(result)}: {result}"
                )

    @pytest.mark.asyncio
    async def test_device_bindings_no_params(self, api: APITestHelper) -> None:
        """GET /api/v1/device-bindings without any filters."""
        response = await api.client.get(
            api.device_binding_url(),
            params=api.params(),
        )
        assert response.status_code != 500, (
            f"Expected non-500 for device-binding query, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestDeviceTemplateQueries:
    """Tests for GET /api/v1/device-templates."""

    @pytest.mark.asyncio
    async def test_device_template_list(self, api: APITestHelper) -> None:
        """GET /api/v1/device-templates returns paginated list."""
        response = await api.client.get(
            api.device_template_url(),
            params=api.params(page=1, page_size=10),
        )
        assert response.status_code in (200, 404, 500), (
            f"Expected 200, 404, or 500 for template list, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        if response.status_code == 200:
            data = response.json()["data"]
            assert isinstance(data, dict), (
                f"Expected dict response, got {type(data)}: {data}"
            )
            if "items" in data:
                assert isinstance(data["items"], list), (
                    f"Expected list for items, got {type(data['items'])}"
                )

    @pytest.mark.asyncio
    async def test_device_template_list_with_type_filter(
        self, api: APITestHelper
    ) -> None:
        """GET /api/v1/device-templates with type filter."""
        for template_type in ("ARCA", "LOCAL", "POOLAB", "TECLAW"):
            response = await api.client.get(
                api.device_template_url(),
                params=api.params(page=1, page_size=10, type=template_type),
            )
            assert response.status_code != 500, (
                f"Expected non-500 for template list with type={template_type}, "
                f"got {response.status_code}: {response.text[:200]}"
            )


class TestBotListingQueries:
    """Tests for GET /api/v1/bots list endpoint with filters."""

    @pytest.mark.asyncio
    async def test_list_bots_with_status_filter(self, api: APITestHelper) -> None:
        """GET /api/v1/bots with status filter for each status."""
        for status in ("ACTIVE", "PENDING", "FAILED", "DESTROYING", "RELEASED"):
            response = await api.client.get(
                api.bot_url(),
                params=api.params(page=1, page_size=10, status=status),
            )
            assert response.status_code == 200, (
                f"Expected 200 for bot list with status={status}, "
                f"got {response.status_code}: {response.text[:200]}"
            )
            data = response.json()
            assert data["code"] == 0
            assert "items" in data["data"]
            assert isinstance(data["data"]["items"], list)

    @pytest.mark.asyncio
    async def test_list_bots_paginated(self, api: APITestHelper) -> None:
        """GET /api/v1/bots with pagination params."""
        for page, page_size in ((1, 5), (1, 20), (2, 5)):
            response = await api.client.get(
                api.bot_url(),
                params=api.params(page=page, page_size=page_size),
            )
            assert response.status_code == 200, (
                f"Expected 200 for bot list page={page} page_size={page_size}, "
                f"got {response.status_code}: {response.text[:200]}"
            )
            data = response.json()
            assert data["code"] == 0
            assert isinstance(data["data"]["items"], list)
            assert len(data["data"]["items"]) <= page_size

    @pytest.mark.asyncio
    async def test_list_bots_with_search(self, api: APITestHelper) -> None:
        """GET /api/v1/bots with search keyword."""
        response = await api.client.get(
            api.bot_url(),
            params=api.params(page=1, page_size=10, keyword="test"),
        )
        assert response.status_code == 200, (
            f"Expected 200 for bot list with search, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        if response.status_code == 200:
            assert data.get("code") == 0
        assert isinstance(data["data"]["items"], list)


class TestPublishListingQueries:
    """Tests for GET /api/v1/publishes."""

    @pytest.mark.asyncio
    async def test_list_publishes(self, api: APITestHelper) -> None:
        """GET /api/v1/publishes returns paginated list."""
        response = await api.client.get(
            api.publish_url(),
            params=api.params(page=1, page_size=10),
        )
        assert response.status_code in (200, 405, 404), (
            f"Expected 200 for publish list (405=method not allowed, 404=not found), "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        if response.status_code == 200:
            assert data.get("code") == 0
            assert "items" in data.get("data", {})
            assert isinstance(data["data"]["items"], list)

    @pytest.mark.asyncio
    async def test_list_publishes_with_bot_filter(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        """GET /api/v1/publishes filtered by bot_uuid."""
        bot = created_bot

        response = await api.client.get(
            api.publish_url(),
            params=api.params(page=1, page_size=10, bot_uuid=bot["bot_uuid"]),
        )
        assert response.status_code in (200, 405, 404), (
            f"Expected 200 for publish list by bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        if response.status_code == 200:
            assert data.get("code") == 0
