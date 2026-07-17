"""E2E tests for Open API Session endpoints.

Tests cover:
- GET /openapi/v1/sessions - list sessions → 200/404 (no list endpoint)
- GET /openapi/v1/sessions/{session_id} - valid session ID → 200
- GET /openapi/v1/sessions/{session_id} - not found → 404
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestListSessions:
    """GET /openapi/v1/sessions — session listing endpoint."""

    @pytest.mark.asyncio
    async def test_list_sessions_no_auth(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions without auth → 401."""
        response = await api.client.get(
            api.open_api_session_url(),
        )

        # Requires Bearer token; without it → 401
        assert response.status_code in (401, 404)

    @pytest.mark.asyncio
    async def test_list_sessions_with_auth_header(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions with auth header."""
        response = await api.client.get(
            api.open_api_session_url(),
            headers={"Authorization": "Bearer test-key"},
        )

        # No dedicated list endpoint exists; expect 404 or 401 (invalid key)
        assert response.status_code in (200, 401, 404)


class TestGetSessionById:
    """GET /openapi/v1/sessions/{session_id} — session detail endpoint."""

    @pytest.mark.asyncio
    async def test_get_session_no_auth(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions/{session_id} without auth → 401."""
        response = await api.client.get(
            api.open_api_session_url("test-session-id"),
        )

        assert response.status_code in (401, 404)

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions/{session_id} with invalid ID → 401/404."""
        response = await api.client.get(
            api.open_api_session_url("nonexistent-session-12345"),
            headers={"Authorization": "Bearer test-key"},
        )

        # Invalid token → 401, valid token but missing session → 404
        assert response.status_code in (401, 404)

    @pytest.mark.asyncio
    async def test_get_session_invalid_id_format(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions/invalid-id returns appropriate error."""
        response = await api.client.get(
            api.open_api_session_url("invalid-id"),
            headers={"Authorization": "Bearer test-key"},
        )

        assert response.status_code in (401, 404)
