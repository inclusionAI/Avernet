"""E2E tests for Bot Session endpoints - normal path.

Tests session listing and detail retrieval for existing bots:
- GET /api/v1/bots/{bot_uuid}/sessions       — List bot sessions
- GET /api/v1/bots/{bot_uuid}/sessions/{id}  — Get session detail
"""

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestSessionNormal:
    """Normal-path bot session tests."""

    @pytest.mark.asyncio
    async def test_list_bot_sessions(self, api: APITestHelper) -> None:
        """GET /bots/{bot_uuid}/sessions returns 200 with paginated session list."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        sessions_data = data["data"]
        assert "items" in sessions_data

    @pytest.mark.asyncio
    async def test_session_list_structure(self, api: APITestHelper) -> None:
        """Session list response has expected pagination structure."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data

        assert isinstance(data["items"], list)

        if data["items"]:
            session = data["items"][0]
            assert "id" in session or "session_id" in session

    @pytest.mark.asyncio
    async def test_get_session_detail(self, api: APITestHelper) -> None:
        """GET /bots/{bot_uuid}/sessions/{id} returns session detail."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        list_response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"]),
            params=api.params(page=1, page_size=5),
        )

        assert list_response.status_code == 200
        items = list_response.json()["data"]["items"]
        if not items:
            pytest.skip("Bot has no sessions")

        session_id = items[0].get("id") or items[0].get("session_id")
        if not session_id:
            pytest.skip("Session has no id")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"], session_id),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert "id" in data or "session_id" in data

    @pytest.mark.asyncio
    async def test_session_list_default_page_size(self, api: APITestHelper) -> None:
        """Session list returns default page size when none specified."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["page_size"] > 0
        assert data["page"] == 1
