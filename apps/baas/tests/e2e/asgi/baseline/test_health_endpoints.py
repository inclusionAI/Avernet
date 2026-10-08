"""E2E tests for health check and hello endpoints.

Tests the basic application endpoints defined in app.py:
- GET /health - Application health check
- GET /hello - Application hello/greeting
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestHealthEndpoint:
    """Tests for GET /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_healthy(self, api: APITestHelper) -> None:
        """GET /health returns 200 with healthy status."""
        response = await api.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_response_structure(self, api: APITestHelper) -> None:
        """GET /health response contains expected fields."""
        response = await api.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        # Should have at minimum a status field
        assert "status" in data


class TestHelloEndpoint:
    """Tests for GET /hello endpoint."""

    @pytest.mark.asyncio
    async def test_hello_returns_200(self, api: APITestHelper) -> None:
        """GET /hello returns 200."""
        response = await api.client.get("/hello")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_hello_response_structure(self, api: APITestHelper) -> None:
        """GET /hello returns a non-empty response."""
        response = await api.client.get("/hello")
        assert response.status_code == 200
        text = response.text
        assert len(text) > 0
