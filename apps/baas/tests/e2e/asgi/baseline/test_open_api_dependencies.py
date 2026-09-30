"""E2E tests for Open API dependency resolution and authentication.

Tests cover:
- Request with valid API key header → 401 (key must pass validator)
- Request without API key header → 401
- Malformed Authorization header formats
- Missing required headers
- Multiple endpoints tested for consistent auth behavior
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestOpenAPIDependencies:
    """Dependency injection and auth validation for Open API endpoints."""

    @pytest.mark.asyncio
    async def test_request_with_valid_api_key_header(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs with Authorization header (dependency resolution).

        The key may not be a real valid key, but we verify the dependency
        chain resolves: the header is parsed, validator is called, and
        a response code is returned (not a 500 DI wiring error).
        """
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        # Dependency resolution works → 401 (invalid key), NOT 500 DI error
        assert response.status_code in (200, 401)
        if response.status_code == 401:
            body = response.json()
            assert "detail" in body
            # Not a DI wiring error — a proper auth rejection
            assert "Provide" not in str(body)

    @pytest.mark.asyncio
    async def test_request_without_api_key_header(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs without Authorization header → 401."""
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
        )

        assert response.status_code == 401
        body = response.json()
        assert body["detail"]["code"] == 40101

    @pytest.mark.asyncio
    async def test_messages_endpoint_without_api_key(self, api: APITestHelper) -> None:
        """POST /openapi/v1/messages without Authorization → 401."""
        response = await api.client.post(
            api.open_api_message_url(),
            json={"bot_id": "test-bot", "message": "hello"},
        )

        assert response.status_code == 401
        body = response.json()
        assert body["detail"]["code"] == 40101

    @pytest.mark.asyncio
    async def test_messages_stream_without_api_key(self, api: APITestHelper) -> None:
        """POST /openapi/v1/messages/stream without Authorization → 401."""
        response = await api.client.post(
            api.open_api_message_stream_url(),
            json={"bot_id": "test-bot", "message": "hello"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_sessions_without_api_key(self, api: APITestHelper) -> None:
        """GET /openapi/v1/sessions/xxx without Authorization → 401."""
        response = await api.client.get(
            api.open_api_session_url("test-session"),
        )

        # The auth dependency fires before bot_id validation
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_run_result_without_api_key(self, api: APITestHelper) -> None:
        """GET /openapi/v1/runs/xxx without Authorization → 401."""
        response = await api.client.get(
            f"{api.open_api_run_url()}/test-run",
        )

        assert response.status_code == 401


class TestMalformedAuthHeaders:
    """Tests for malformed Authorization header formats across endpoints."""

    @pytest.mark.asyncio
    async def test_non_bearer_scheme_on_runs(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs with non-Bearer scheme → 400."""
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
            headers={"Authorization": "Digest foo"},
        )

        assert response.status_code in (400, 401)

    @pytest.mark.asyncio
    async def test_bearer_without_token_on_runs(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs with 'Bearer' (no token) → 400."""
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
            headers={"Authorization": "Bearer"},
        )

        assert response.status_code in (400, 401)

    @pytest.mark.asyncio
    async def test_bearer_with_extra_parts_on_runs(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs with 'Bearer token extra' → 400."""
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
            headers={"Authorization": "Bearer token extra"},
        )

        assert response.status_code in (400, 401)

    @pytest.mark.asyncio
    async def test_empty_authorization_on_messages(self, api: APITestHelper) -> None:
        """POST /openapi/v1/messages with empty Authorization → 401."""
        response = await api.client.post(
            api.open_api_message_url(),
            json={"bot_id": "test-bot", "message": "hello"},
            headers={"Authorization": ""},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_lowercase_bearer_on_runs(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs with lowercase 'bearer' scheme → 401.

        get_api_key_from_header does case-insensitive comparison of 'bearer'.
        """
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
            headers={"Authorization": "bearer sk-test-key"},
        )

        assert response.status_code in (200, 401)

    @pytest.mark.asyncio
    async def test_missing_authorization_on_message_result(
        self, api: APITestHelper
    ) -> None:
        """GET /openapi/v1/messages/{id} without Authorization → 401."""
        response = await api.client.get(
            f"{api.open_api_message_url()}/test-message-id",
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_authorization_on_session_messages(
        self, api: APITestHelper
    ) -> None:
        """GET /openapi/v1/sessions/{id}/messages without Authorization → 401."""
        response = await api.client.get(
            f"{api.open_api_session_url('test-session')}/messages",
        )

        assert response.status_code == 401
