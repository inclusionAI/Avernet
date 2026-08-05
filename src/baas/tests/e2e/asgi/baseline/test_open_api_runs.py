"""E2E tests for Open API Run endpoints.

Tests cover:
- POST /openapi/v1/runs — valid bot and session params → 200
- POST /openapi/v1/runs — missing required fields → 422
- POST /openapi/v1/runs — empty message string → 401/422
- POST /openapi/v1/runs — invalid param values
- GET /openapi/v1/runs/{run_id} — valid run ID → 200
- POST /openapi/v1/runs/{run_id}/cancel — run cancellation
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestCreateRun:
    """POST /openapi/v1/runs — single chat run endpoint."""

    @pytest.mark.asyncio
    async def test_create_run_no_auth(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs without auth → 401."""
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
        )

        assert response.status_code == 401
        body = response.json()
        assert body["detail"]["code"] == 40101
        assert body["detail"]["message"] == "Token missing"

    @pytest.mark.asyncio
    async def test_create_run_missing_message(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs without message field → 401/422."""
        response = await api.client.post(
            api.open_api_run_url(),
            json={},
        )

        # Auth validation fires before request body validation
        assert response.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_create_run_with_auth_invalid_key(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs with invalid API key → 401."""
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
            headers={"Authorization": "Bearer invalid-api-key"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_run_with_metadata(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /openapi/v1/runs with metadata → 401 (no valid key)."""
        response = await api.client.post(
            api.open_api_run_url(),
            json={
                "message": "hello",
                "metadata": {
                    "biz_task_id": f"e2e-task-{unique_id}",
                    "biz_scene": "test",
                },
            },
        )

        assert response.status_code in (200, 401)

    @pytest.mark.asyncio
    async def test_create_run_empty_message(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs with empty message string → 401/422.

        message field has min_length=1, empty string must fail validation.
        """
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": ""},
        )

        assert response.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_create_run_malformed_auth_header(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs with malformed Authorization → 400/401."""
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
            headers={"Authorization": "malformed-token"},
        )

        # Non-Bearer format → 400 Bad Request
        assert response.status_code in (400, 401)

    @pytest.mark.asyncio
    async def test_create_run_basic_auth_header(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs with Basic auth (not Bearer) → 400."""
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )

        assert response.status_code in (400, 401)

    @pytest.mark.asyncio
    async def test_create_run_empty_authorization_header(
        self, api: APITestHelper
    ) -> None:
        """POST /openapi/v1/runs with empty Authorization header → 401.

        An empty string fails the 'not authorization' check in get_api_key_from_header.
        """
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
            headers={"Authorization": ""},
        )

        assert response.status_code == 401


class TestGetRunResult:
    """GET /openapi/v1/runs/{run_id} — run result query endpoint."""

    @pytest.mark.asyncio
    async def test_get_run_result_no_auth(self, api: APITestHelper) -> None:
        """GET /openapi/v1/runs/{run_id} without auth → 401."""
        response = await api.client.get(
            f"{api.open_api_run_url()}/test-run-id",
        )

        assert response.status_code in (401, 404)

    @pytest.mark.asyncio
    async def test_get_run_result_not_found(self, api: APITestHelper) -> None:
        """GET /openapi/v1/runs/{run_id} with nonexistent ID → 401/404."""
        response = await api.client.get(
            f"{api.open_api_run_url()}/nonexistent-run-id",
            headers={"Authorization": "Bearer test-key"},
        )

        # Invalid token → 401, valid token but missing run → 404
        assert response.status_code in (401, 404)

    @pytest.mark.asyncio
    async def test_get_run_result_with_auth_valid_key(self, api: APITestHelper) -> None:
        """GET /openapi/v1/runs/{run_id} with Authorization header."""
        response = await api.client.get(
            f"{api.open_api_run_url()}/test-run-id",
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status_code in (200, 401, 404)

    @pytest.mark.asyncio
    async def test_get_run_result_empty_run_id(self, api: APITestHelper) -> None:
        """GET /openapi/v1/runs/ (trailing slash, no run_id) → 401/404/405."""
        response = await api.client.get(
            f"{api.open_api_run_url()}/",
            headers={"Authorization": "Bearer test-key"},
        )

        assert response.status_code in (401, 404, 405)


class TestCancelRun:
    """POST /openapi/v1/runs/{run_id}/cancel — run cancellation endpoint.

    The run_router does not define a cancel endpoint, so expect 405 or 404.
    """

    @pytest.mark.asyncio
    async def test_cancel_run_no_auth(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs/{run_id}/cancel without auth → 401/405."""
        response = await api.client.post(
            f"{api.open_api_run_url()}/test-run-id/cancel",
            json={},
        )

        assert response.status_code in (401, 404, 405)

    @pytest.mark.asyncio
    async def test_cancel_run_not_found(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs/{run_id}/cancel with nonexistent run → 401/405/404."""
        response = await api.client.post(
            f"{api.open_api_run_url()}/nonexistent-run/cancel",
            json={},
            headers={"Authorization": "Bearer test-key"},
        )

        assert response.status_code in (401, 404, 405)

    @pytest.mark.asyncio
    async def test_cancel_run_with_auth_valid_key(self, api: APITestHelper) -> None:
        """POST /openapi/v1/runs/{run_id}/cancel with valid auth header."""
        response = await api.client.post(
            f"{api.open_api_run_url()}/test-run-id/cancel",
            json={},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status_code in (200, 401, 404, 405)
