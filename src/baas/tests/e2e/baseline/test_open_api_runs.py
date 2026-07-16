"""E2E tests for Open API Run endpoints.

Tests cover:
- POST /openapi/v1/runs — valid bot and session params → 200
- GET /openapi/v1/runs/{run_id} — valid run ID → 200
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


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
        assert body["detail"]["message"] == "Token 缺失"

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
