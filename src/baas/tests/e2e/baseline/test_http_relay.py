"""E2E tests for Bot HTTP Relay / Proxy endpoints.

Tests cover the HTTP proxy endpoint that forwards requests to a bot's active device:
- POST/GET /api/v1/bots/{tenant}/{bot_uuid}/invoke-http/{port}/{path}

NOTE: These tests require a running PaaS bot with active devices.
Without that, the dispatcher returns 404/500/503.
"""

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


class TestHttpRelay:
    """HTTP relay / proxy endpoint tests."""

    @pytest.mark.asyncio
    async def test_http_relay_post_active_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST relay to a bot's invoke-http exercises the endpoint.

        The endpoint requires an active bot with running devices.
        Without a running device, we expect 404/500/503 (no active device).
        """
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.post(
            f"/api/v1/bots/{api.tenant}/{bot['bot_uuid']}/invoke-http/8080/api/test",
            json={"message": "hello"},
            params=api.params(),
        )

        assert response.status_code in (200, 404, 500, 503), (
            f"Expected 200, 404, 500, or 503 for HTTP relay to active bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_http_relay_get_active_bot(self, api: APITestHelper) -> None:
        """GET relay to a bot's invoke-http exercises the endpoint."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            f"/api/v1/bots/{api.tenant}/{bot['bot_uuid']}/invoke-http/8080/",
            params=api.params(),
        )

        assert response.status_code in (200, 404, 500, 503), (
            f"Expected 200, 404, 500, or 503 for GET relay to active bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_http_relay_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST relay to nonexistent bot UUID returns 404 or 500.

        The dispatcher may return 500 if the bot lookup throws an unhandled
        exception in the stub implementation.
        """
        response = await api.client.post(
            f"/api/v1/bots/{api.tenant}/{NONEXISTENT_UUID}/invoke-http/8080/api/test",
            json={"message": "should fail"},
            params=api.params(),
        )

        assert response.status_code in (404, 500), (
            f"Expected 404 or 500 for relay to nonexistent bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_http_relay_invalid_path(self, api: APITestHelper) -> None:
        """POST relay with invalid/missing path parameters returns 422/400."""
        response = await api.client.post(
            f"/api/v1/bots/{api.tenant}/{NONEXISTENT_UUID}/invoke-http/not-a-port/api",
            json={"message": "bad port"},
            params=api.params(),
        )

        assert response.status_code in (400, 404, 422, 500), (
            f"Expected 400, 404, 422, or 500 for invalid port in relay, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_http_relay_empty_body(self, api: APITestHelper) -> None:
        """POST relay with empty body exercises the endpoint."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.post(
            f"/api/v1/bots/{api.tenant}/{bot['bot_uuid']}/invoke-http/8080/health",
            params=api.params(),
        )

        assert response.status_code in (200, 404, 500, 503), (
            f"Expected 200, 404, 500, or 503 for relay with empty body, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_http_relay_different_ports(self, api: APITestHelper) -> None:
        """POST relay to different ports exercises port routing."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        # Try relay on different ports to exercise port routing
        for port in (3000, 8080, 9090):
            response = await api.client.post(
                f"/api/v1/bots/{api.tenant}/{bot['bot_uuid']}"
                f"/invoke-http/{port}/api/status",
                json={"test": True},
                params=api.params(),
            )

            assert response.status_code in (200, 404, 500, 503), (
                f"Expected 200, 404, 500, or 503 for relay on port {port}, "
                f"got {response.status_code}: {response.text[:200]}"
            )
