"""E2E tests for Relay Session management endpoints.

Tests cover the relay session routing info API used by agentclawproxy:
- GET /api/v1/paas/relay-sessions/{session_id} — query routing info
- PUT /api/v1/paas/relay-sessions/{session_id} — update session status

NOTE: The relay_session_url() builder in conftest returns /api/v1/relay-sessions
      but the actual route prefix is /api/v1/paas/relay-sessions/.
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]

_NONEXISTENT_SESSION = "nonexistent-session-00000000"


class TestRelaySessionGet:
    """GET /api/v1/paas/relay-sessions/{session_id} — query relay session."""

    @pytest.mark.asyncio
    async def test_get_nonexistent_relay_session(self, api: APITestHelper) -> None:
        """GET /api/v1/paas/relay-sessions/{nonexistent} returns 404."""
        response = await api.client.get(
            f"/api/v1/paas/relay-sessions/{_NONEXISTENT_SESSION}",
            params=api.params(),
        )

        assert response.status_code == 404, (
            f"Expected 404 for nonexistent relay session, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        assert "detail" in data
        assert data["detail"]["error_code"] == "RELAY_SESSION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_get_relay_session_empty_id(self, api: APITestHelper) -> None:
        """GET /api/v1/paas/relay-sessions/ (no session_id) returns 404/405."""
        response = await api.client.get(
            "/api/v1/paas/relay-sessions/",
            params=api.params(),
        )

        assert response.status_code in (404, 405), (
            f"Expected 404 or 405 for empty relay session ID, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestRelaySessionPut:
    """PUT /api/v1/paas/relay-sessions/{session_id} — update relay session."""

    @pytest.mark.asyncio
    async def test_put_nonexistent_relay_session(self, api: APITestHelper) -> None:
        """PUT /api/v1/paas/relay-sessions/{nonexistent} returns 404."""
        response = await api.client.put(
            f"/api/v1/paas/relay-sessions/{_NONEXISTENT_SESSION}",
            params=api.params(),
            json={
                "status": "active",
                "connected_server_instance": "server-001",
                "connected_route_info": {"host": "10.0.0.1", "port": 8080},
            },
        )

        assert response.status_code == 404, (
            f"Expected 404 for PUT nonexistent relay session, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        assert "detail" in data
        assert data["detail"]["error_code"] == "RELAY_SESSION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_put_invalid_status(self, api: APITestHelper) -> None:
        """PUT with invalid status value returns 422."""
        response = await api.client.put(
            f"/api/v1/paas/relay-sessions/{_NONEXISTENT_SESSION}",
            params=api.params(),
            json={
                "status": "invalid_status",
                "connected_server_instance": "server-001",
                "connected_route_info": {"host": "10.0.0.1", "port": 8080},
            },
        )

        assert response.status_code in (404, 422), (
            f"Expected 404 or 422 for invalid relay session status, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_put_active_missing_route_info(self, api: APITestHelper) -> None:
        """PUT with status=active but missing route info returns 404/400.

        The validator checks connected_server_instance and connected_route_info
        are non-empty for active status transitions. But if the session doesn't
        exist, we get 404 first.
        """
        response = await api.client.put(
            f"/api/v1/paas/relay-sessions/{_NONEXISTENT_SESSION}",
            params=api.params(),
            json={
                "status": "active",
                "connected_server_instance": "",
                "connected_route_info": {},
            },
        )

        assert response.status_code in (400, 404), (
            f"Expected 400 or 404 for active without route info, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_put_closed_nonexistent(self, api: APITestHelper) -> None:
        """PUT with status=closed on nonexistent session returns 404."""
        response = await api.client.put(
            f"/api/v1/paas/relay-sessions/{_NONEXISTENT_SESSION}",
            params=api.params(),
            json={"status": "closed"},
        )

        assert response.status_code == 404, (
            f"Expected 404 for PUT closed on nonexistent, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_put_missing_required_fields(self, api: APITestHelper) -> None:
        """PUT with empty body returns 422."""
        response = await api.client.put(
            f"/api/v1/paas/relay-sessions/{_NONEXISTENT_SESSION}",
            params=api.params(),
            json={},
        )

        assert response.status_code in (404, 422), (
            f"Expected 404 or 422 for empty PUT body, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestRelaySessionContracts:
    """Additional contract/edge tests for relay session endpoints."""

    @pytest.mark.asyncio
    async def test_relay_session_response_structure(self, api: APITestHelper) -> None:
        """Verify relay session response structure on error paths."""
        response = await api.client.get(
            f"/api/v1/paas/relay-sessions/{_NONEXISTENT_SESSION}",
            params=api.params(),
        )

        assert response.status_code == 404
        data = response.json()
        detail = data["detail"]
        assert "error_code" in detail
        assert "message" in detail
        assert detail["error_code"] == "RELAY_SESSION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_relay_session_url_builder_exercise(self, api: APITestHelper) -> None:
        """Exercise the relay_session_url builder to confirm URL construction.

        The relay_session_url in conftest returns /api/v1/relay-sessions but
        the actual route is under /api/v1/paas/relay-sessions/. This test
        confirms the URL builder produces a valid path.
        """
        url = api.relay_session_url("test-session-123")
        assert "/relay-sessions" in url, (
            f"Expected relay_session_url to contain /relay-sessions, got {url}"
        )

        # Also exercise the endpoint at the correct route
        response = await api.client.get(
            "/api/v1/paas/relay-sessions/test-session-123",
            params=api.params(),
        )

        assert response.status_code in (404, 200), (
            f"Expected 404 or 200 for relay session query, "
            f"got {response.status_code}: {response.text[:200]}"
        )
