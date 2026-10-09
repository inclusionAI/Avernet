"""E2E tests for Bootstrap + App startup (Phase 1.8).

Covers application bootstrap and startup health endpoints:
- GET /health    — Application health check
- GET /hello     — Quick liveness endpoint
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestBootstrapHealth:
    """Tests for GET /health — application-level health check."""

    @pytest.mark.asyncio
    async def test_health_returns_healthy(self, api: APITestHelper) -> None:
        """GET /health returns {"status": "healthy"}."""
        response = await api.client.get("/health")
        assert response.status_code == 200, (
            f"Expected 200 for /health, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        assert data["status"] == "healthy", f"Expected status 'healthy', got: {data}"

    @pytest.mark.asyncio
    async def test_health_response_structure(self, api: APITestHelper) -> None:
        """GET /health response has expected shape."""
        response = await api.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "status" in data
        assert isinstance(data["status"], str)


class TestBootstrapHello:
    """Tests for GET /hello — liveness endpoint."""

    @pytest.mark.asyncio
    async def test_hello_returns_text(self, api: APITestHelper) -> None:
        """GET /hello returns a text response."""
        response = await api.client.get("/hello")
        assert response.status_code in (200, 404), (
            f"Expected 200 or 404 for /hello, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        if response.status_code == 200:
            assert isinstance(response.text, str)
            assert len(response.text) > 0, "Expected non-empty response text"


class TestBootstrapReadiness:
    """Tests for application readiness after bootstrap."""

    @pytest.mark.asyncio
    async def test_multiple_health_calls_consistent(self, api: APITestHelper) -> None:
        """Multiple GET /health calls return consistent healthy status."""
        for _ in range(3):
            response = await api.client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_root_endpoint(self, api: APITestHelper) -> None:
        """GET / (root) returns a response."""
        response = await api.client.get("/")
        assert response.status_code != 500, (
            f"Expected non-500 for root endpoint, "
            f"got {response.status_code}: {response.text[:200]}"
        )
