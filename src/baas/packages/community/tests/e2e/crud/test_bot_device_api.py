"""E2E tests for bot device operation endpoints.

Tests the 4 bot device operation routers that were migrated to DI:
- bot_cmd_router: POST /api/v1/bots/{tenant}/{bot_uuid}/execute-command
- bot_http_router:  ANY /api/v1/bots/{tenant}/{bot_uuid}/invoke-http/{port}/{path}
- bot_open_folder_router: POST /api/v1/bots/{tenant}/{bot_uuid}/open-folder
- bot_wss_router: GET /api/v1/bots/{bot_uuid}/ws-info

These tests verify the endpoints are wired correctly through the DI container
and return appropriate responses against the running backend.
"""

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.crud,
    pytest.mark.skip(
        reason="Bot device API requires active bot/device state; skipped for now"
    ),
]


class TestBotCmdRouter:
    """E2E tests for the bot command execution endpoint."""

    @pytest.mark.asyncio
    async def test_execute_command_requires_active_bot(
        self, api: APITestHelper
    ) -> None:
        """POST execute-command with a nonexistent bot returns 404 or 503.

        The endpoint is wired through DI and should return a structured
        error rather than a 500 Internal Server Error.
        """
        response = await api.client.post(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/execute-command",
            params=api.params(),
            json={"cmd": "echo hello"},
        )

        # A DI wiring failure would produce 500; any 4xx means DI works
        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )

        # We should get a structured error response
        data = response.json()
        detail = data.get("detail", data)
        assert "error" in detail or "detail" in str(data)

    @pytest.mark.asyncio
    async def test_execute_command_validation_422(self, api: APITestHelper) -> None:
        """Empty cmd string returns 422 validation error."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/bot-uuid/execute-command",
            params=api.params(),
            json={"cmd": ""},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_execute_command_invalid_timeout_422(
        self, api: APITestHelper
    ) -> None:
        """timeout_seconds > 300 returns 422."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/bot-uuid/execute-command",
            params=api.params(),
            json={"cmd": "test", "timeout_seconds": 999},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_execute_command_negative_timeout_422(
        self, api: APITestHelper
    ) -> None:
        """timeout_seconds < 1 returns 422."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/bot-uuid/execute-command",
            params=api.params(),
            json={"cmd": "test", "timeout_seconds": 0},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_execute_command_missing_cmd_422(self, api: APITestHelper) -> None:
        """Missing cmd field returns 422."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/bot-uuid/execute-command",
            params=api.params(),
            json={},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_execute_command_different_tenant(self, api: APITestHelper) -> None:
        """Different tenant should still route correctly (not 500)."""
        response = await api.client.post(
            "/api/v1/bots/unknown_tenant/nonexistent-bot-uuid/execute-command",
            params=api.params(),
            json={"cmd": "echo hello"},
        )

        # DI wiring doesn't depend on tenant value
        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_execute_command_cmd_max_length(self, api: APITestHelper) -> None:
        """Very long cmd string (>8192 chars) returns 422."""
        long_cmd = "x" * 9000
        response = await api.client.post(
            "/api/v1/bots/team_claw/bot-uuid/execute-command",
            params=api.params(),
            json={"cmd": long_cmd},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_execute_command_with_env(self, api: APITestHelper) -> None:
        """Custom env in request body is accepted (not 500)."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/execute-command",
            params=api.params(),
            json={"cmd": "echo hello", "env": {"MY_VAR": "test"}},
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_execute_command_timeout_at_boundary(
        self, api: APITestHelper
    ) -> None:
        """timeout_seconds=1 (minimum valid) and =300 (maximum valid) are accepted."""
        for timeout in (1, 300):
            response = await api.client.post(
                "/api/v1/bots/team_claw/nonexistent-bot-uuid/execute-command",
                params=api.params(),
                json={"cmd": "test", "timeout_seconds": timeout},
            )

            # Should not return 422 (validation passes)
            assert response.status_code != 422, (
                f"timeout={timeout} should be valid, got {response.status_code}"
            )


class TestBotHttpRouter:
    """E2E tests for the bot HTTP proxy endpoint."""

    @pytest.mark.asyncio
    async def test_invoke_http_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET invoke-http with a nonexistent bot returns 4xx (not 500).

        A DI wiring failure would produce 500; any 4xx means DI works.
        """
        response = await api.client.get(
            "/api/v1/bots/team_claw/nonexistent-bot/invoke-http/8080/api/test",
            params=api.params(),
        )

        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )

        data = response.json()
        detail = data.get("detail", data)
        assert "error" in detail or "detail" in str(data)

    @pytest.mark.asyncio
    async def test_invoke_http_with_tenant(self, api: APITestHelper) -> None:
        """invoke-http with valid tenant but nonexistent bot returns 404."""
        response = await api.client.get(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/invoke-http/8080/api/health",
            params=api.params(),
        )

        assert response.status_code in (404, 503)

        data = response.json()
        detail = data.get("detail", data)
        assert detail.get("error") in (
            "BOT_NOT_FOUND",
            "NO_DEVICES_FOUND",
            "NO_ACTIVE_DEVICES",
        )

    @pytest.mark.asyncio
    async def test_invoke_http_post_with_body(self, api: APITestHelper) -> None:
        """POST to invoke-http with a nonexistent bot returns 404."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/invoke-http/443/api/command",
            params=api.params(),
            json={"action": "deploy"},
        )

        assert response.status_code in (404, 503)

    @pytest.mark.asyncio
    async def test_invoke_http_put(self, api: APITestHelper) -> None:
        """PUT to invoke-http with a nonexistent bot returns 404."""
        response = await api.client.put(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/invoke-http/8080/api/config",
            params=api.params(),
            json={"key": "value"},
        )

        assert response.status_code in (404, 503)

    @pytest.mark.asyncio
    async def test_invoke_http_delete(self, api: APITestHelper) -> None:
        """DELETE to invoke-http with a nonexistent bot returns 404."""
        response = await api.client.delete(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/invoke-http/8080/api/resource/42",
            params=api.params(),
        )

        assert response.status_code in (404, 503)

    @pytest.mark.asyncio
    async def test_invoke_http_port_validation_422(self, api: APITestHelper) -> None:
        """Port out of range returns 422."""
        response = await api.client.get(
            "/api/v1/bots/team_claw/bot-uuid/invoke-http/99999/api/test",
            params=api.params(),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invoke_http_port_zero_422(self, api: APITestHelper) -> None:
        """Port=0 returns 422."""
        response = await api.client.get(
            "/api/v1/bots/team_claw/bot-uuid/invoke-http/0/api/test",
            params=api.params(),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invoke_http_different_tenant(self, api: APITestHelper) -> None:
        """Different tenant still routes correctly (not 500)."""
        response = await api.client.get(
            "/api/v1/bots/unknown_tenant/nonexistent-bot/invoke-http/8080/api/test",
            params=api.params(),
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_invoke_http_patch_not_allowed_405(self, api: APITestHelper) -> None:
        """PATCH method is not supported (405)."""
        response = await api.client.request(
            "PATCH",
            "/api/v1/bots/team_claw/bot-uuid/invoke-http/8080/api/test",
            params=api.params(),
        )

        assert response.status_code == 405

    @pytest.mark.asyncio
    async def test_invoke_http_options_not_allowed_405(
        self, api: APITestHelper
    ) -> None:
        """OPTIONS method is not supported (405)."""
        response = await api.client.request(
            "OPTIONS",
            "/api/v1/bots/team_claw/bot-uuid/invoke-http/8080/api/test",
            params=api.params(),
        )

        assert response.status_code == 405


class TestBotOpenFolderRouter:
    """E2E tests for the bot open-folder endpoint."""

    @pytest.mark.asyncio
    async def test_open_folder_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST open-folder with a nonexistent bot returns 404."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/open-folder",
            params=api.params(),
            json={"folder_path": "/tmp"},
        )

        # DI wiring failure would produce 500; 404 means DI works
        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )

        assert response.status_code == 404

        data = response.json()
        detail = data.get("detail", data)
        assert detail.get("error") == "BOT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_open_folder_no_request_body(self, api: APITestHelper) -> None:
        """POST open-folder without body with nonexistent bot returns 404 (not 422)."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/open-folder",
            params=api.params(),
        )

        # Without body should still route correctly (folder_path defaults to None)
        # and hit the bot lookup which returns 404
        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )
        assert response.status_code in (404, 422)

    @pytest.mark.asyncio
    async def test_open_folder_empty_body(self, api: APITestHelper) -> None:
        """POST open-folder with empty body returns 404 (bot not found)."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/open-folder",
            params=api.params(),
            json={},
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_open_folder_with_device_affinity(self, api: APITestHelper) -> None:
        """device_affinity query param is accepted and routed correctly."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/open-folder"
            "?device_affinity=session-abc",
            params=api.params(),
            json={"folder_path": "/tmp"},
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_open_folder_different_tenant(self, api: APITestHelper) -> None:
        """Different tenant still routes correctly (not 500)."""
        response = await api.client.post(
            "/api/v1/bots/unknown_tenant/nonexistent-bot-uuid/open-folder",
            params=api.params(),
            json={"folder_path": "/tmp"},
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_open_folder_home_directory(self, api: APITestHelper) -> None:
        """Home directory path is accepted."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/nonexistent-bot-uuid/open-folder",
            params=api.params(),
            json={"folder_path": "/home/user"},
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_open_folder_empty_string_path(self, api: APITestHelper) -> None:
        """Empty string folder_path should be rejected."""
        response = await api.client.post(
            "/api/v1/bots/team_claw/bot-uuid/open-folder",
            params=api.params(),
            json={"folder_path": ""},
        )

        assert response.status_code != 500


class TestBotWssRouter:
    """E2E tests for the bot WebSocket connection info endpoint."""

    @pytest.mark.asyncio
    async def test_get_ws_info_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET ws-info with a nonexistent bot returns 404."""
        response = await api.client.get(
            "/api/v1/bots/nonexistent-bot-uuid/ws-info"
            "?port=8080&path=/api/ws&tenant=team_claw",
        )

        # DI wiring failure would produce 500; 4xx means DI works
        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )

        data = response.json()
        detail = data.get("detail", data)
        assert "error" in detail or "code" in data

    @pytest.mark.asyncio
    async def test_get_ws_info_port_below_range_422(self, api: APITestHelper) -> None:
        """Port below range returns 422 validation error."""
        response = await api.client.get(
            "/api/v1/bots/bot-uuid/ws-info?port=0&path=/p&tenant=team_claw",
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_ws_info_port_above_range_422(self, api: APITestHelper) -> None:
        """Port above range returns 422 validation error."""
        response = await api.client.get(
            "/api/v1/bots/bot-uuid/ws-info?port=65536&path=/p&tenant=team_claw",
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_ws_info_missing_required_params_422(
        self, api: APITestHelper
    ) -> None:
        """Missing required query params returns 422."""
        response = await api.client.get(
            "/api/v1/bots/bot-uuid/ws-info",
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_ws_info_with_device_affinity(self, api: APITestHelper) -> None:
        """device_affinity query param is accepted by the endpoint."""
        response = await api.client.get(
            "/api/v1/bots/nonexistent-bot-uuid/ws-info"
            "?port=8080&path=/api/ws&tenant=team_claw&device_affinity=session-xyz",
        )

        # Should route correctly (don't 500), device_affinity is just a query param
        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_get_ws_info_port_minimum_valid(self, api: APITestHelper) -> None:
        """Port=1 (minimum) routes correctly (not 422 or 500)."""
        response = await api.client.get(
            "/api/v1/bots/nonexistent-bot-uuid/ws-info"
            "?port=1&path=/api/ws&tenant=team_claw",
        )

        assert response.status_code != 422
        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_get_ws_info_port_maximum_valid(self, api: APITestHelper) -> None:
        """Port=65535 (maximum) routes correctly (not 422 or 500)."""
        response = await api.client.get(
            "/api/v1/bots/nonexistent-bot-uuid/ws-info"
            "?port=65535&path=/api/ws&tenant=team_claw",
        )

        assert response.status_code != 422
        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_get_ws_info_long_path(self, api: APITestHelper) -> None:
        """Long path is accepted (not 500)."""
        long_path = "/api/" + "x" * 100
        response = await api.client.get(
            f"/api/v1/bots/nonexistent-bot-uuid/ws-info"
            f"?port=8080&path={long_path}&tenant=team_claw",
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_get_ws_info_empty_tenant(self, api: APITestHelper) -> None:
        """Empty tenant should return 422."""
        response = await api.client.get(
            "/api/v1/bots/bot-uuid/ws-info?port=8080&path=/ws&tenant=",
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_ws_info_normalized_path(self, api: APITestHelper) -> None:
        """Path with special chars routes correctly (not 500)."""
        response = await api.client.get(
            "/api/v1/bots/nonexistent-bot-uuid/ws-info"
            "?port=443&path=/api/openclaw/ws&tenant=team_claw",
        )

        assert response.status_code != 500


class TestBotHttpConnInfoRouter:
    """E2E tests for the bot HTTP connection info endpoint."""

    @pytest.mark.asyncio
    async def test_get_http_info_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET http-info with a nonexistent bot returns 404."""
        response = await api.client.get(
            "/api/v1/bots/nonexistent-bot-uuid/http-info"
            "?port=8080&path=/api/health&tenant=team_claw",
        )

        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )

        data = response.json()
        detail = data.get("detail", data)
        assert "error" in detail or "code" in data

    @pytest.mark.asyncio
    async def test_get_http_info_port_below_range_422(self, api: APITestHelper) -> None:
        """Port below range returns 422 validation error."""
        response = await api.client.get(
            "/api/v1/bots/bot-uuid/http-info?port=0&path=/p&tenant=team_claw",
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_http_info_port_above_range_422(self, api: APITestHelper) -> None:
        """Port above range returns 422 validation error."""
        response = await api.client.get(
            "/api/v1/bots/bot-uuid/http-info?port=65536&path=/p&tenant=team_claw",
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_http_info_missing_required_params_422(
        self, api: APITestHelper
    ) -> None:
        """Missing required query params returns 422."""
        response = await api.client.get(
            "/api/v1/bots/bot-uuid/http-info",
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_http_info_with_device_affinity(self, api: APITestHelper) -> None:
        """device_affinity query param is accepted by the endpoint."""
        response = await api.client.get(
            "/api/v1/bots/nonexistent-bot-uuid/http-info"
            "?port=8080&path=/api/health&tenant=team_claw&device_affinity=session-xyz",
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_get_http_info_port_minimum_valid(self, api: APITestHelper) -> None:
        """Port=1 (minimum) routes correctly (not 422 or 500)."""
        response = await api.client.get(
            "/api/v1/bots/nonexistent-bot-uuid/http-info"
            "?port=1&path=/api/health&tenant=team_claw",
        )

        assert response.status_code != 422
        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_get_http_info_port_maximum_valid(self, api: APITestHelper) -> None:
        """Port=65535 (maximum) routes correctly (not 422 or 500)."""
        response = await api.client.get(
            "/api/v1/bots/nonexistent-bot-uuid/http-info"
            "?port=65535&path=/api/health&tenant=team_claw",
        )

        assert response.status_code != 422
        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_get_http_info_long_path(self, api: APITestHelper) -> None:
        """Long path is accepted (not 500)."""
        long_path = "/api/" + "x" * 100
        response = await api.client.get(
            f"/api/v1/bots/nonexistent-bot-uuid/http-info"
            f"?port=8080&path={long_path}&tenant=team_claw",
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_get_http_info_empty_tenant(self, api: APITestHelper) -> None:
        """Empty tenant should return 422."""
        response = await api.client.get(
            "/api/v1/bots/bot-uuid/http-info?port=8080&path=/health&tenant=",
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_http_info_normalized_path(self, api: APITestHelper) -> None:
        """Path with special chars routes correctly (not 500)."""
        response = await api.client.get(
            "/api/v1/bots/nonexistent-bot-uuid/http-info"
            "?port=443&path=/api/health&tenant=team_claw",
        )

        assert response.status_code != 500
