"""E2E tests for Bot Session endpoints - edge cases.

Tests cover boundary and edge-case behavior:
- Pagination boundary values
- Edge session ID formats
"""

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestSessionEdge:
    """Edge-case tests for bot session endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="TODO: bot_sessions endpoint not implemented in sofa mode")
    async def test_session_pagination_boundary(self, api: APITestHelper) -> None:
        """Session list handles pagination at extreme values."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"]),
            params=api.params(page=1, page_size=1),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["page"] == 1
        assert data["page_size"] == 1
        assert isinstance(data["items"], list)

        assert len(data["items"]) <= 1

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="TODO: bot_sessions endpoint not implemented in sofa mode")
    async def test_session_pagination_large_page(self, api: APITestHelper) -> None:
        """Session list with large page_size is handled gracefully."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"]),
            params=api.params(page=1, page_size=9999),
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="TODO: bot_sessions endpoint not implemented in sofa mode")
    async def test_session_pagination_page_beyond_range(
        self, api: APITestHelper
    ) -> None:
        """Session list with page beyond available data returns empty list."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"]),
            params=api.params(page=9999, page_size=10),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["items"] == []

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="TODO: bot_sessions endpoint not implemented in sofa mode")
    async def test_session_empty_list_bot_with_no_sessions(
        self, api: APITestHelper
    ) -> None:
        """Bot with no sessions returns empty items list."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"]),
            params=api.params(page=1, page_size=1),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="TODO: bot_sessions endpoint not implemented in sofa mode")
    async def test_session_list_page_size_limit(self, api: APITestHelper) -> None:
        """Session list page_size is bounded to a reasonable maximum."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"]),
            params=api.params(page=1, page_size=500),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["page_size"] <= 500
