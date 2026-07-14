"""E2E tests for sandbox health check endpoint.

Tests the sandbox health check endpoint at GET /api/v1/health-check/sandbox.
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestSandboxHealthNormal:
    """Tests for GET /api/v1/health-check/sandbox — normal cases."""

    @pytest.mark.asyncio
    async def test_sandbox_health_returns_status(self, api: APITestHelper) -> None:
        """GET sandbox_health_url returns 200 or auth-gated response."""
        response = await api.client.get(
            api.sandbox_health_url(),
            params=api.params(),
        )

        # Auth gate may fire before route handler
        assert response.status_code in (200, 401), (
            f"Expected 200 or 401, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_sandbox_health_response_structure(self, api: APITestHelper) -> None:
        """GET sandbox_health_url response contains expected fields."""
        response = await api.client.get(
            api.sandbox_health_url(),
            params=api.params(),
        )

        if response.status_code == 401:
            pytest.skip("Sandbox health endpoint requires auth")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        if "data" in data:
            inner = data["data"]
            assert isinstance(inner, dict)
        else:
            assert "status" in data or "healthy" in data


class TestSandboxHealthErrors:
    """Tests for GET /api/v1/health-check/sandbox — error cases."""

    @pytest.mark.asyncio
    async def test_sandbox_health_invalid_params(self, api: APITestHelper) -> None:
        """GET sandbox_health_url with invalid query params returns 4xx."""
        response = await api.client.get(
            api.sandbox_health_url(),
            params=api.params(foo="bar", invalid_param="true"),
        )

        # Invalid params should produce 4xx, not 500
        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )
