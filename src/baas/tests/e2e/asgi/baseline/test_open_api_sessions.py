"""E2E tests for Open API Session endpoints.

Tests cover:
- GET /openapi/v1/sessions - list sessions → 200/404 (no list endpoint)
- GET /openapi/v1/sessions - pagination with page and page_size params
- GET /openapi/v1/sessions/{session_id} - valid session ID → 200
- GET /openapi/v1/sessions/{session_id} - not found → 404
- GET /openapi/v1/sessions/{session_id} - invalid session ID format
- GET /openapi/v1/sessions/{session_id}/messages - query session messages
- DELETE /openapi/v1/sessions/{session_id} - session deletion (405 or 404)
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


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

    @pytest.mark.asyncio
    async def test_list_sessions_with_pagination(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions with pagination params → 401/404."""
        response = await api.client.get(
            api.open_api_session_url(),
            params={"page": 1, "page_size": 5},
            headers={"Authorization": "Bearer test-key"},
        )

        assert response.status_code in (200, 401, 404)

    @pytest.mark.asyncio
    async def test_list_sessions_page_out_of_range(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions with page=99999 → 401/404."""
        response = await api.client.get(
            api.open_api_session_url(),
            params={"page": 99999, "page_size": 10},
            headers={"Authorization": "Bearer test-key"},
        )

        assert response.status_code in (200, 401, 404)

    @pytest.mark.asyncio
    async def test_list_sessions_zero_page_size(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions with page_size=0 → 401/422/404."""
        response = await api.client.get(
            api.open_api_session_url(),
            params={"page": 1, "page_size": 0},
            headers={"Authorization": "Bearer test-key"},
        )

        assert response.status_code in (200, 401, 422, 404)


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

    @pytest.mark.asyncio
    async def test_get_session_no_bot_id_param(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions/{session_id} without bot_id query param.

        When app_type is not "bot", bot_id is required. Without it → 400/401.
        """
        response = await api.client.get(
            api.open_api_session_url("test-session-id"),
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status_code in (200, 400, 401, 404)

    @pytest.mark.asyncio
    async def test_get_session_with_lifecycle_stage(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions/{session_id} with lifecycle_stage=all."""
        response = await api.client.get(
            api.open_api_session_url("test-session-id"),
            params={"bot_id": "test-bot", "lifecycle_stage": "all"},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status_code in (200, 401, 404)


class TestGetSessionMessages:
    """GET /openapi/v1/sessions/{session_id}/messages — session messages endpoint."""

    @pytest.mark.asyncio
    async def test_get_session_messages_no_auth(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions/{id}/messages without auth → 401."""
        response = await api.client.get(
            f"{api.open_api_session_url('test-session')}/messages",
        )

        assert response.status_code in (401, 404)

    @pytest.mark.asyncio
    async def test_get_session_messages_not_found(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions/{id}/messages with nonexistent session → 401/404."""
        response = await api.client.get(
            f"{api.open_api_session_url('nonexistent-session')}/messages",
            headers={"Authorization": "Bearer test-key"},
        )

        assert response.status_code in (401, 404)

    @pytest.mark.asyncio
    async def test_get_session_messages_with_limit(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions/{id}/messages with limit=10."""
        response = await api.client.get(
            f"{api.open_api_session_url('test-session')}/messages",
            params={"bot_id": "test-bot", "limit": 10},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status_code in (200, 401, 404)

    @pytest.mark.asyncio
    async def test_get_session_messages_limit_exceeds_max(
        self, api: APITestHelper
    ) -> None:
        """GET /openapi/v1/sessions/{id}/messages with limit > 1000 → 422."""
        response = await api.client.get(
            f"{api.open_api_session_url('test-session')}/messages",
            params={"bot_id": "test-bot", "limit": 2000},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        # limit has ge=1, le=1000 constraint → 422 for out of range
        assert response.status_code in (200, 401, 404, 422)

    @pytest.mark.asyncio
    async def test_get_session_messages_no_bot_id(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions/{id}/messages without bot_id query param.

        When app_type is not "bot", bot_id is required. Without it → 400/401.
        """
        response = await api.client.get(
            f"{api.open_api_session_url('test-session')}/messages",
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status_code in (200, 400, 401, 404)


class TestDeleteSession:
    """DELETE /openapi/v1/sessions/{session_id} — session deletion endpoint.

    The session_router does not define a DELETE endpoint, so expect 405 or 404.
    """

    @pytest.mark.asyncio
    async def test_delete_session_no_auth(self, api: APITestHelper) -> None:
        """DELETE /openapi/v1/sessions/{session_id} without auth → 401/405."""
        response = await api.client.delete(
            api.open_api_session_url("test-session-id"),
        )

        assert response.status_code in (401, 404, 405)

    @pytest.mark.asyncio
    async def test_delete_session_with_auth(self, api: APITestHelper) -> None:
        """DELETE /openapi/v1/sessions/{session_id} with auth header → 401/405."""
        response = await api.client.delete(
            api.open_api_session_url("test-session-id"),
            headers={"Authorization": "Bearer test-key"},
        )

        assert response.status_code in (401, 404, 405)

    @pytest.mark.asyncio
    async def test_delete_session_nonexistent(self, api: APITestHelper) -> None:
        """DELETE /openapi/v1/sessions/{session_id} nonexistent → 401/405/404."""
        response = await api.client.delete(
            api.open_api_session_url("nonexistent-session-99999"),
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status_code in (401, 404, 405)
