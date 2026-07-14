"""E2E tests for Bot Session endpoints - error paths.

Tests that invalid session requests return appropriate error responses:
- GET /api/v1/bots/{nonexistent}/sessions    — Non-existent bot
- GET /api/v1/bots/{uuid}/sessions/{bad_id}  — Non-existent session
"""

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"
FAKE_SESSION_ID = "SESSION-nonexistent-0000000000"


class TestSessionErrors:
    """Error-handling tests for bot session endpoints."""

    @pytest.mark.asyncio
    async def test_list_sessions_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET /bots/{nonexistent}/sessions returns 404."""
        response = await api.client.get(
            api.bot_sessions_url(NONEXISTENT_UUID),
            params=api.params(),
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, api: APITestHelper) -> None:
        """GET /bots/{uuid}/sessions/{bad_id} returns 404."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"], FAKE_SESSION_ID),
            params=api.params(),
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.asyncio
    async def test_cross_tenant_session_access(self, api: APITestHelper) -> None:
        """GET /bots/{uuid}/sessions with different tenant returns 404 or 4xx."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"]),
            params={"tenant": "unknown_other_tenant"},
        )

        assert response.status_code != 500
        assert response.status_code != 200

    @pytest.mark.asyncio
    async def test_list_sessions_invalid_uuid_format(self, api: APITestHelper) -> None:
        """GET /bots/{bad_uuid}/sessions with malformed UUID returns 404 or 422."""
        response = await api.client.get(
            api.bot_sessions_url("not-a-valid-uuid-at-all"),
            params=api.params(),
        )

        assert response.status_code != 500
        assert response.status_code in (404, 422)

    @pytest.mark.asyncio
    async def test_get_session_empty_id(self, api: APITestHelper) -> None:
        """GET /bots/{uuid}/sessions/ with empty session ID returns 404 or 405."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_sessions_url(bot["bot_uuid"], ""),
            params=api.params(),
        )

        assert response.status_code in (404, 405)
