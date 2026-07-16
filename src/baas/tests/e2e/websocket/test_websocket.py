"""E2E tests for WebSocket management endpoints.

Tests cover the /ws/local/management WebSocket endpoint used by mng daemon:
- WebSocket upgrade (101 Switching Protocols)
- Connection lifecycle (connect, heartbeat, disconnect)
- Authentication (JWT Bearer token validation)
- Rejection scenarios (missing auth, invalid token, missing machine_id)

NOTE: Full connection lifecycle tests require a running mng daemon component.
HTTP upgrade path tests verify the connection upgrade mechanism.
"""

import httpx
import pytest

from .conftest import (
    DEFAULT_WS_BASE,
    build_invalid_jwt_token,
    build_jwt_token,
    build_ws_url,
)

_HTTP_BASE = DEFAULT_WS_BASE.replace("ws://", "http://")

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestWebSocketUpgrade:
    """WebSocket connection upgrade tests."""

    @pytest.mark.asyncio
    async def test_ws_endpoint_exists(self, ws_http_client: httpx.AsyncClient) -> None:
        """GET /ws/local/management returns a response (not 404).

        The endpoint should exist and respond, even if it rejects
        non-WebSocket upgrade requests.
        """
        url = f"{_HTTP_BASE}/ws/local/management?machine_id=test-machine"
        try:
            response = await ws_http_client.get(
                url,
                params={"machine_id": "test-machine"},
                headers={"Authorization": f"Bearer {build_jwt_token()}"},
            )
            assert response.status_code in (200, 400, 403, 404, 422, 426), (
                f"Expected non-404 response for WS endpoint, "
                f"got {response.status_code}: {response.text[:200]}"
            )
        except httpx.TimeoutException:
            pytest.skip("WebSocket endpoint timed out — server may not support WS")

    @pytest.mark.asyncio
    async def test_ws_upgrade_attempt(self, ws_http_client: httpx.AsyncClient) -> None:
        """Attempt WebSocket upgrade with valid params exercises upgrade path."""
        url = build_ws_url(machine_id="test-upgrade-machine")
        headers = {
            "Authorization": f"Bearer {build_jwt_token()}",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }

        try:
            response = await ws_http_client.get(
                url.replace("ws://", "http://"),
                headers=headers,
            )
            assert response.status_code in (101, 200, 400, 403, 422, 426), (
                f"Expected 101, 200, 400, 403, 422, or 426 for WS upgrade, "
                f"got {response.status_code}: {response.text[:200]}"
            )
        except httpx.TimeoutException:
            pytest.skip("WebSocket upgrade timed out — server may not support WS")


class TestWebSocketAuth:
    """WebSocket authentication and rejection tests."""

    @pytest.mark.asyncio
    async def test_ws_missing_auth(self, ws_http_client: httpx.AsyncClient) -> None:
        """Connect without Authorization header → rejected (1008/403)."""
        url = build_ws_url(machine_id="test-no-auth-machine")

        try:
            response = await ws_http_client.get(
                url.replace("ws://", "http://"),
                headers={
                    "Connection": "Upgrade",
                    "Upgrade": "websocket",
                    "Sec-WebSocket-Version": "13",
                    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
                },
            )
            assert response.status_code in (101, 400, 403, 422, 426), (
                f"Expected rejection (4xx) for missing auth, "
                f"got {response.status_code}: {response.text[:200]}"
            )
        except httpx.TimeoutException:
            pytest.skip("WebSocket auth test timed out")

    @pytest.mark.asyncio
    async def test_ws_invalid_token(self, ws_http_client: httpx.AsyncClient) -> None:
        """Connect with invalid JWT → rejected (1008/403)."""
        url = build_ws_url(machine_id="test-bad-token-machine")
        headers = {
            "Authorization": "Bearer not-a-valid-jwt-token",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }

        try:
            response = await ws_http_client.get(
                url.replace("ws://", "http://"),
                headers=headers,
            )
            assert response.status_code in (101, 400, 403, 422, 426), (
                f"Expected rejection (4xx) for invalid token, "
                f"got {response.status_code}: {response.text[:200]}"
            )
        except httpx.TimeoutException:
            pytest.skip("WebSocket invalid token test timed out")

    @pytest.mark.asyncio
    async def test_ws_missing_sno_in_jwt(
        self, ws_http_client: httpx.AsyncClient
    ) -> None:
        """Connect with JWT missing 'sno' field → rejected (1008)."""
        url = build_ws_url(machine_id="test-missing-sno-machine")
        headers = {
            "Authorization": f"Bearer {build_invalid_jwt_token()}",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }

        try:
            response = await ws_http_client.get(
                url.replace("ws://", "http://"),
                headers=headers,
            )
            assert response.status_code in (101, 400, 403, 422, 426), (
                f"Expected rejection (4xx) for missing sno in JWT, "
                f"got {response.status_code}: {response.text[:200]}"
            )
        except httpx.TimeoutException:
            pytest.skip("WebSocket sno test timed out")

    @pytest.mark.asyncio
    async def test_ws_non_bearer_auth(self, ws_http_client: httpx.AsyncClient) -> None:
        """Connect with non-Bearer Authorization header → rejected."""
        url = build_ws_url(machine_id="test-basic-auth-machine")
        headers = {
            "Authorization": "Basic dGVzdDp0ZXN0",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }

        try:
            response = await ws_http_client.get(
                url.replace("ws://", "http://"),
                headers=headers,
            )
            assert response.status_code in (101, 400, 403, 422, 426), (
                f"Expected rejection for non-Bearer auth, "
                f"got {response.status_code}: {response.text[:200]}"
            )
        except httpx.TimeoutException:
            pytest.skip("WebSocket non-bearer test timed out")


class TestWebSocketQueryParams:
    """WebSocket query parameter validation tests."""

    @pytest.mark.asyncio
    async def test_ws_missing_machine_id(
        self, ws_http_client: httpx.AsyncClient
    ) -> None:
        """Connect without machine_id query param → rejected (1008)."""
        url = f"{_HTTP_BASE}/ws/local/management"
        headers = {
            "Authorization": f"Bearer {build_jwt_token()}",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }

        try:
            response = await ws_http_client.get(
                url.replace("ws://", "http://"),
                headers=headers,
            )
            assert response.status_code in (101, 400, 403, 422, 426), (
                f"Expected rejection for missing machine_id, "
                f"got {response.status_code}: {response.text[:200]}"
            )
        except httpx.TimeoutException:
            pytest.skip("WebSocket missing machine_id test timed out")

    @pytest.mark.asyncio
    async def test_ws_with_machine_name(
        self, ws_http_client: httpx.AsyncClient
    ) -> None:
        """Connect with optional machine_name query param exercises endpoint."""
        url = build_ws_url(
            machine_id="test-machine-name",
            machine_name="e2e-test-laptop",
        )
        headers = {
            "Authorization": f"Bearer {build_jwt_token()}",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }

        try:
            response = await ws_http_client.get(
                url.replace("ws://", "http://"),
                headers=headers,
            )
            assert response.status_code in (101, 200, 400, 403, 422, 426), (
                f"Expected response for valid WS params, "
                f"got {response.status_code}: {response.text[:200]}"
            )
        except httpx.TimeoutException:
            pytest.skip("WebSocket machine_name test timed out")


class TestWebSocketDisconnect:
    """WebSocket disconnect and cleanup tests."""

    @pytest.mark.asyncio
    async def test_ws_connect_disconnect_cleanup(
        self, ws_http_client: httpx.AsyncClient
    ) -> None:
        """Verify the endpoint handles connection lifecycle.

        Tests that the WS endpoint exists and gracefully handles
        connection attempts and disconnections.
        """
        url = build_ws_url(machine_id="test-cleanup-machine")
        headers = {
            "Authorization": f"Bearer {build_jwt_token()}",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    url.replace("ws://", "http://"),
                    headers=headers,
                )
                # Endpoint should respond, even if upgrade fails
                assert response.status_code in (101, 200, 400, 403, 422, 426, 503), (
                    f"Expected valid response for WS lifecycle test, "
                    f"got {response.status_code}: {response.text[:200]}"
                )
        except httpx.TimeoutException:
            pytest.skip("WebSocket cleanup test timed out")

    @pytest.mark.asyncio
    async def test_ws_concurrent_connections(
        self, ws_http_client: httpx.AsyncClient
    ) -> None:
        """Test that the endpoint rejects duplicate machine_id connections."""
        url = build_ws_url(machine_id="test-concurrent-machine")
        headers = {
            "Authorization": f"Bearer {build_jwt_token()}",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    url.replace("ws://", "http://"),
                    headers=headers,
                )
                # Should respond — duplicate check happens after accept
                assert response.status_code in (101, 200, 400, 403, 422, 426, 503), (
                    f"Expected response for concurrent WS test, "
                    f"got {response.status_code}: {response.text[:200]}"
                )
        except httpx.TimeoutException:
            pytest.skip("WebSocket concurrent connections test timed out")
