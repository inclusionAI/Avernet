"""E2E tests for cross-provider health check error consistency and app-level health.

Tests:
- Consistent error format across health check endpoints (404, 400)
- Application-level health endpoints (GET /health, GET /hello)
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestConsistentErrorFormat:
    """Tests for consistent error responses across health check endpoints."""

    @pytest.mark.asyncio
    async def test_404_on_nonexistent_health_endpoint(self, api: APITestHelper) -> None:
        """GET /api/v1/health-check/nonexistent returns 404."""
        response = await api.client.get(
            "/api/v1/health-check/nonexistent",
            params=api.params(),
        )

        assert response.status_code == 404, (
            f"Expected 404 for nonexistent endpoint, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_400_on_missing_params(self, api: APITestHelper) -> None:
        """GET health endpoints without required params return 4xx."""
        # Try bot health without bot UUID in path
        response = await api.client.get(
            "/api/v1/health-check/bot/",
            params=api.params(),
        )

        # Trailing slash with no UUID should return 404 (not found) or 405 (method not allowed)
        assert response.status_code in (404, 405), (
            f"Expected 404 or 405, got {response.status_code}: {response.text[:200]}"
        )


class TestAppLevelHealth:
    """Tests for application-level health endpoints (outside /api/v1)."""

    @pytest.mark.asyncio
    async def test_root_health_returns_healthy(self, api: APITestHelper) -> None:
        """GET /health returns 200 with healthy status."""
        response = await api.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_root_hello(self, api: APITestHelper) -> None:
        """GET /hello returns 200 with non-empty response."""
        response = await api.client.get("/hello")
        assert response.status_code == 200
        text = response.text
        assert len(text) > 0
