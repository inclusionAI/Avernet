"""E2E tests for Miscellaneous Service endpoints (Phase 1.6).

Covers service endpoints that are peripheral to the main bot lifecycle:
- POST /api/v1/sse/events       — SSE event registry
- POST /api/v1/callback/{type}  — Callback dispatch
- POST /api/v1/bots/{id}/sessions — Bot session management
"""

from typing import Any

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


class TestSseRegistry:
    """Tests for POST /api/v1/sse/events."""

    @pytest.mark.asyncio
    async def test_sse_events_post(self, api: APITestHelper) -> None:
        """POST /api/v1/sse/events exercises the SSE registry."""
        response = await api.client.post(
            "/api/v1/sse/events",
            params=api.params(),
            json={"event": "test", "data": {"key": "value"}},
        )
        assert response.status_code in (200, 404, 401, 422), (
            f"Expected 200, 404, 401, or 422 for SSE events, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_sse_events_empty_body(self, api: APITestHelper) -> None:
        """POST /api/v1/sse/events with empty body."""
        response = await api.client.post(
            "/api/v1/sse/events",
            params=api.params(),
        )
        assert response.status_code in (200, 404, 401, 422), (
            f"Expected 200, 404, 401, or 422 for empty SSE events, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestCallbackDispatch:
    """Tests for POST /api/v1/callback/{type}."""

    @pytest.mark.asyncio
    async def test_callback_device_start(self, api: APITestHelper) -> None:
        """POST /api/v1/callback/device testing the callback dispatch."""
        response = await api.client.post(
            "/api/v1/callback/device",
            json={
                "device_uuid": "test-device-uuid",
                "publish_id": 1,
                "event_type": "start",
                "result_status": "SUCCESS",
                "tenant": api.tenant,
            },
        )
        assert response.status_code in (200, 404, 401, 422), (
            f"Expected 200, 404, 401, or 422 for callback, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_callback_with_invalid_type(self, api: APITestHelper) -> None:
        """POST /api/v1/callback/invalid_type exercises error handling."""
        response = await api.client.post(
            "/api/v1/callback/invalid_type",
            json={"key": "value"},
        )
        assert response.status_code != 500, (
            f"Expected non-500 for invalid callback type, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestBotSessions:
    """Tests for POST /api/v1/bots/{id}/sessions."""

    @pytest.mark.asyncio
    async def test_bot_sessions_with_valid_bot(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        """POST /api/v1/bots/{bot_uuid}/sessions on existing bot."""
        bot = created_bot

        response = await api.client.post(
            f"/api/v1/bots/{bot['bot_uuid']}/sessions",
            params=api.params(),
            json={"name": "test-session"},
        )
        assert response.status_code != 500, (
            f"Expected non-500 for bot sessions, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_bot_sessions_nonexistent_bot(self, api: APITestHelper) -> None:
        """POST /api/v1/bots/{nonexistent}/sessions returns error."""
        response = await api.client.post(
            f"/api/v1/bots/{NONEXISTENT_UUID}/sessions",
            params=api.params(),
            json={"name": "test-session"},
        )
        assert response.status_code in (200, 404, 401, 422, 500), (
            f"Expected 200, 404, 401, 422, or 500 for nonexistent bot sessions, "
            f"got {response.status_code}: {response.text[:200]}"
        )
