"""E2E tests for Bot Runtime dispatcher endpoints.

Tests that dispatcher-related endpoints work correctly:
- GET  /api/v1/bots/{bot_uuid}/http-conn     — HTTP connection info
- GET  /api/v1/bots/{bot_uuid}/wss-conn       — WSS connection info
- POST /api/v1/bots/{bot_uuid}/cmd             — Send command
- POST /api/v1/bots/{bot_uuid}/open-folder     — Open folder
- GET  /api/v1/bots/{bot_uuid}/start-progress  — Start progress
"""

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


class TestHttpConnInfo:
    """HTTP connection info endpoint tests."""

    pytestmark = pytest.mark.dispatcher

    @pytest.mark.asyncio
    async def test_get_http_conn_info(self, api: APITestHelper) -> None:
        """GET /bots/{bot_uuid}/http-conn returns connection info."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_http_conn_url(bot["bot_uuid"]),
            params=api.params(port=8080, path="/api"),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    @pytest.mark.asyncio
    async def test_http_conn_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET /bots/{nonexistent}/http-info returns 404."""
        response = await api.client.get(
            api.bot_http_conn_url(NONEXISTENT_UUID),
            params=api.params(port=8080, path="/api"),
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error"] == "BOT_NOT_FOUND"


class TestWssConnInfo:
    """WSS connection info endpoint tests."""

    pytestmark = pytest.mark.dispatcher

    @pytest.mark.asyncio
    async def test_get_wss_conn_info(self, api: APITestHelper) -> None:
        """GET /bots/{bot_uuid}/wss-conn returns WSS connection info."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_wss_conn_url(bot["bot_uuid"]),
            params=api.params(port=8080, path="/ws"),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    @pytest.mark.asyncio
    async def test_wss_conn_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET /bots/{nonexistent}/ws-info returns 404."""
        response = await api.client.get(
            api.bot_wss_conn_url(NONEXISTENT_UUID),
            params=api.params(port=8080, path="/ws"),
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error"] == "BOT_NOT_FOUND"


class TestCmdEndpoint:
    """CMD endpoint tests."""

    pytestmark = pytest.mark.dispatcher

    @pytest.mark.asyncio
    async def test_post_cmd(self, api: APITestHelper) -> None:
        """POST /bots/{tenant}/{bot_uuid}/execute-command accepts a command body."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.post(
            api.bot_cmd_url(bot["bot_uuid"]),
            params=api.params(),
            json={"cmd": "ls"},
        )

        assert response.status_code in (200, 202)
        data = response.json()
        assert "code" in data

    @pytest.mark.asyncio
    async def test_cmd_empty_body(self, api: APITestHelper) -> None:
        """POST /bots/{tenant}/{bot_uuid}/execute-command with empty body returns 422."""
        response = await api.client.post(
            api.bot_cmd_url(NONEXISTENT_UUID),
            params=api.params(),
        )

        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_cmd_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST /bots/{tenant}/{nonexistent}/execute-command returns 404."""
        response = await api.client.post(
            api.bot_cmd_url(NONEXISTENT_UUID),
            params=api.params(),
            json={"cmd": "ls"},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error"] == "BOT_NOT_FOUND"


class TestOpenFolder:
    """Open-folder endpoint tests."""

    pytestmark = pytest.mark.dispatcher

    @pytest.mark.asyncio
    async def test_post_open_folder(self, api: APITestHelper) -> None:
        """POST /bots/{tenant}/{bot_uuid}/open-folder accepts a folder path."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.post(
            api.bot_open_folder_url(bot["bot_uuid"]),
            params=api.params(),
            json={"folder_path": "/tmp"},
        )

        assert response.status_code in (200, 202)
        data = response.json()
        assert "code" in data

    @pytest.mark.asyncio
    async def test_open_folder_nonexistent(self, api: APITestHelper) -> None:
        """POST /bots/{tenant}/{nonexistent}/open-folder returns 404."""
        response = await api.client.post(
            api.bot_open_folder_url(NONEXISTENT_UUID),
            params=api.params(),
            json={"folder_path": "/tmp"},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error"] == "BOT_NOT_FOUND"


class TestStartProgress:
    """Start-progress endpoint tests."""

    pytestmark = pytest.mark.dispatcher

    @pytest.mark.asyncio
    async def test_get_start_progress(self, api: APITestHelper) -> None:
        """GET /bots/{bot_uuid}/start-progress returns progress info."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_start_progress_url(bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    @pytest.mark.asyncio
    async def test_start_progress_nonexistent(self, api: APITestHelper) -> None:
        """GET /bots/{nonexistent}/start-progress returns 404."""
        response = await api.client.get(
            api.bot_start_progress_url(NONEXISTENT_UUID),
            params=api.params(),
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error"] == "BOT_NOT_FOUND"
