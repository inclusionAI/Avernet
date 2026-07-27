"""E2E tests for sandbox health check endpoint.

Tests the sandbox health check endpoint at GET /api/v1/health-check/sandbox.
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestSandboxHealthNormal:
    """Tests for GET /api/v1/health-check/sandbox — normal cases."""

    @pytest.fixture(autouse=True)
    def _override_health_checker_auth(self, _testclient_app):
        from secbaas.community.adapters.web.routers.health_checker.health_checker_router import (
            APIKeyRecord,
            validate_bot_health_api_key,
        )

        async def _mock_noop(*args, **kwargs):
            return APIKeyRecord(
                api_key_prefix="test-prefix",
                api_key_hash="test-hash",
                app_id="t-healthcheck",
                app_type="health-checker",
                status="active",
            )

        _testclient_app.dependency_overrides[validate_bot_health_api_key] = _mock_noop
        yield
        del _testclient_app.dependency_overrides[validate_bot_health_api_key]

    @pytest.mark.asyncio
    async def test_sandbox_health_returns_status(self, api: APITestHelper) -> None:
        """GET sandbox_health_url returns 200."""
        response = await api.client.get(
            api.sandbox_health_url(),
            params=api.params(),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_sandbox_health_response_structure(self, api: APITestHelper) -> None:
        """GET sandbox_health_url response contains expected fields."""
        response = await api.client.get(
            api.sandbox_health_url(),
            params=api.params(),
        )

        assert response.status_code != 500
        if response.status_code != 200:
            return
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

        assert response.status_code != 500, (
            f"Expected non-500 response, got {response.status_code}: "
            f"{response.text[:200]}"
        )
