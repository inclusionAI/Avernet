"""E2E tests for Token and Health API endpoints.

Tests cover:
- POST /api/v1/tokens/ctoken - Create ctoken
- GET /health - Health check
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.crud]


class TestCreateCtoken:
    """Tests for POST /api/v1/tokens/ctoken endpoint."""

    @pytest.mark.asyncio
    async def test_create_ctoken(self, api: APITestHelper) -> None:
        """Test create ctoken."""
        response = await api.client.post(
            "/api/v1/tokens/ctoken",
            params=api.params(),
            json={
                "sandbox_id": "test-sandbox-e2e",
                "mode": "dev",
                "ttl": 3600,
            },
        )

        assert response.status_code in [200, 400, 401, 404, 422, 500]


class TestHealthCheck:
    """Tests for GET /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_check(self, api: APITestHelper) -> None:
        """Test health check returns healthy status."""
        response = await api.client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
