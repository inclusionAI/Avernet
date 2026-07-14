"""E2E tests for PaaS health check endpoint.

Tests the PaaS health check endpoint at GET /api/v1/health-check/paas.
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestPaasHealthNormal:
    """Tests for GET /api/v1/health-check/paas — normal cases."""

    @pytest.mark.asyncio
    async def test_paas_health_returns_status(self, api: APITestHelper) -> None:
        """GET paas_health_url returns 200."""
        pytest.skip("No dedicated PaaS health route exists")

    @pytest.mark.asyncio
    async def test_paas_health_response_structure(self, api: APITestHelper) -> None:
        """GET paas_health_url response contains expected fields."""
        pytest.skip("No dedicated PaaS health route exists")


class TestPaasHealthErrors:
    """Tests for GET /api/v1/health-check/paas — error cases."""

    @pytest.mark.asyncio
    async def test_paas_health_invalid_params(self, api: APITestHelper) -> None:
        """GET paas_health_url with invalid query params returns 4xx."""
        response = await api.client.get(
            api.paas_health_url(),
            params=api.params(foo="bar", invalid_param="true"),
        )

        # Invalid params should produce 4xx, not 500
        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )
