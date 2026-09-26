"""E2E tests for Bot Service Router endpoints (Phase 1.5).

Covers the bot service-side router endpoints that dispatch operations
to running bot devices:

- POST /api/v1/bots/{bot_uuid}/open-folder   — Open folder on bot device
- POST /api/v1/bots/{bot_uuid}/cmd           — Send command to bot device
- POST /api/v1/bots/{bot_uuid}/http          — HTTP proxy through bot
- POST /api/v1/bots/{bot_uuid}/wss           — WSS relay through bot
- POST /api/v1/bots/{bot_uuid}/http-conn     — HTTP connection info
- POST /api/v1/bots/{bot_uuid}/publish       — Publish bot

Error cases:
- Invalid / nonexistent bot UUID
- Missing required params
"""

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    activate_test_bot,
    cleanup_bot,
    create_test_bot,
)

pytestmark = [pytest.mark.e2e_asgi]

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


class TestOpenFolder:
    """Tests for POST /api/v1/bots/{bot_uuid}/open-folder."""

    @pytest.mark.asyncio
    async def test_open_folder_with_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """POST open-folder on a valid bot exercises the endpoint."""
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            f"/api/v1/bots/{bot_uuid}/open-folder",
            params=api.params(),
            json={"path": "/tmp"},
        )
        assert response.status_code != 500, (
            f"Expected non-500 for open-folder on valid bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_open_folder_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST open-folder on nonexistent bot returns error."""
        response = await api.client.post(
            f"/api/v1/bots/{NONEXISTENT_UUID}/open-folder",
            params=api.params(),
            json={"path": "/tmp"},
        )
        assert response.status_code in (404, 400, 422, 500), (
            f"Expected error status for nonexistent bot open-folder, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_open_folder_missing_path(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """POST open-folder without 'path' should fail validation."""
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            f"/api/v1/bots/{bot_uuid}/open-folder",
            params=api.params(),
            json={},
        )
        assert response.status_code in (400, 404, 422), (
            f"Expected 400/422 for missing path, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestCmd:
    """Tests for POST /api/v1/bots/{bot_uuid}/cmd."""

    @pytest.mark.asyncio
    async def test_cmd_with_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """POST cmd on a valid bot exercises the endpoint."""
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            f"/api/v1/bots/{bot_uuid}/cmd",
            params=api.params(),
            json={"cmd": "echo hello"},
        )
        assert response.status_code != 500, (
            f"Expected non-500 for cmd on valid bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_cmd_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST cmd on nonexistent bot returns error."""
        response = await api.client.post(
            f"/api/v1/bots/{NONEXISTENT_UUID}/cmd",
            params=api.params(),
            json={"cmd": "ls"},
        )
        assert response.status_code in (404, 400, 422, 500), (
            f"Expected error status for nonexistent bot cmd, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_cmd_missing_body(self, api: APITestHelper) -> None:
        """POST cmd with empty body should fail validation."""
        response = await api.client.post(
            f"/api/v1/bots/{NONEXISTENT_UUID}/cmd",
            params=api.params(),
        )
        assert response.status_code in (400, 404, 422), (
            f"Expected 400/404/422 for missing cmd body, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestHttp:
    """Tests for POST /api/v1/bots/{bot_uuid}/http."""

    @pytest.mark.asyncio
    async def test_http_with_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """POST http on a valid bot exercises the endpoint."""
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            f"/api/v1/bots/{bot_uuid}/http",
            params=api.params(),
            json={"port": 8080, "path": "/api/health", "method": "GET"},
        )
        assert response.status_code != 500, (
            f"Expected non-500 for http on valid bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_http_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST http on nonexistent bot returns error."""
        response = await api.client.post(
            f"/api/v1/bots/{NONEXISTENT_UUID}/http",
            params=api.params(),
            json={"port": 8080, "path": "/"},
        )
        assert response.status_code in (404, 400, 422, 500), (
            f"Expected error status for nonexistent bot http, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestWss:
    """Tests for POST /api/v1/bots/{bot_uuid}/wss."""

    @pytest.mark.asyncio
    async def test_wss_with_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """POST wss on a valid bot exercises the endpoint."""
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            f"/api/v1/bots/{bot_uuid}/wss",
            params=api.params(),
            json={"port": 9222, "path": "/devtools"},
        )
        assert response.status_code != 500, (
            f"Expected non-500 for wss on valid bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_wss_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST wss on nonexistent bot returns error."""
        response = await api.client.post(
            f"/api/v1/bots/{NONEXISTENT_UUID}/wss",
            params=api.params(),
            json={"port": 9222, "path": "/ws"},
        )
        assert response.status_code in (404, 400, 422, 500), (
            f"Expected error status for nonexistent bot wss, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestHttpConn:
    """Tests for POST /api/v1/bots/{bot_uuid}/http-conn."""

    @pytest.mark.asyncio
    async def test_http_conn_with_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """POST http-conn on a valid bot exercises the endpoint."""
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            f"/api/v1/bots/{bot_uuid}/http-conn",
            params=api.params(),
            json={"port": 8080, "path": "/api"},
        )
        assert response.status_code != 500, (
            f"Expected non-500 for http-conn on valid bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_http_conn_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST http-conn on nonexistent bot returns error."""
        response = await api.client.post(
            f"/api/v1/bots/{NONEXISTENT_UUID}/http-conn",
            params=api.params(),
            json={"port": 8080, "path": "/"},
        )
        assert response.status_code in (404, 400, 422, 500), (
            f"Expected error status for nonexistent bot http-conn, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestPublish:
    """Tests for POST /api/v1/bots/{bot_uuid}/publish."""

    @pytest.mark.asyncio
    async def test_publish_with_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """POST publish on a valid bot exercises the endpoint."""
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            f"/api/v1/bots/{bot_uuid}/publish",
            params=api.params(),
            json={"operator": "e2e-test"},
        )
        assert response.status_code != 500, (
            f"Expected non-500 for publish on valid bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_publish_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST publish on nonexistent bot returns error."""
        response = await api.client.post(
            f"/api/v1/bots/{NONEXISTENT_UUID}/publish",
            params=api.params(),
            json={"operator": "e2e-test"},
        )
        assert response.status_code in (404, 400, 422, 500), (
            f"Expected error status for nonexistent bot publish, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestBotServiceRouterErrors:
    """Error scenario tests for bot service routers."""

    @pytest.mark.asyncio
    async def test_invalid_bot_uuid_format(self, api: APITestHelper) -> None:
        """POST with an invalid-format UUID should be rejected."""
        response = await api.client.post(
            "/api/v1/bots/not-a-valid-uuid/open-folder",
            params=api.params(),
            json={"path": "/tmp"},
        )
        assert response.status_code in (400, 404, 422), (
            f"Expected 400/404/422 for invalid UUID format, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_missing_params_across_routers(self, api: APITestHelper) -> None:
        """POST to each router with empty body exercises validation."""
        routers = ("open-folder", "cmd", "http", "wss", "http-conn", "publish")
        for router in routers:
            response = await api.client.post(
                f"/api/v1/bots/{NONEXISTENT_UUID}/{router}",
                params=api.params(),
                json={},
            )
            assert response.status_code in (400, 404, 422), (
                f"Expected 400/404/422 for {router} with empty body, "
                f"got {response.status_code}: {response.text[:200]}"
            )
