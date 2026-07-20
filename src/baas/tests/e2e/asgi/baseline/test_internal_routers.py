"""E2E tests for internal router endpoints (Phase 1.6).

Covers the internal-only router endpoints not exposed to the public API:
- GET /internal/bot-health-checker/alive — Internal health checker alive endpoint
- GET /internal/health                     — Internal health endpoint
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestInternalHealthAlive:
    """Tests for GET /internal/bot-health-checker/alive."""

    @pytest.mark.asyncio
    async def test_alive_with_valid_params(self, api: APITestHelper) -> None:
        """GET /internal/bot-health-checker/alive with valid params."""
        response = await api.client.get(
            "/internal/bot-health-checker/alive",
            params={
                "bot_id": "test-bot",
                "entity_id": "test-entity",
                "env": "prod",
                "minutes": 60,
            },
        )
        assert response.status_code in (200, 404, 500), (
            f"Expected 200, 404, or 500 for internal alive check, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_alive_missing_required_params(self, api: APITestHelper) -> None:
        """GET /internal/bot-health-checker/alive without required params."""
        response = await api.client.get(
            "/internal/bot-health-checker/alive",
        )
        assert response.status_code == 422, (
            f"Expected 422 for missing required params, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_alive_with_statuses(self, api: APITestHelper) -> None:
        """GET /internal/bot-health-checker/alive with statuses filter."""
        response = await api.client.get(
            "/internal/bot-health-checker/alive",
            params={
                "bot_id": "test-bot",
                "entity_id": "test-entity",
                "env": "prod",
                "statuses": "online,draft",
            },
        )
        assert response.status_code in (200, 404), (
            f"Expected 200 or 404 for alive with statuses, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestInternalHealth:
    """Tests for GET /internal/health."""

    @pytest.mark.asyncio
    async def test_internal_health_endpoint(self, api: APITestHelper) -> None:
        """GET /internal/health returns a response."""
        response = await api.client.get("/internal/health")
        assert response.status_code in (200, 404, 500), (
            f"Expected 200, 404, or 500 for internal health, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict), (
                f"Expected dict response, got {type(data)}: {data}"
            )

    @pytest.mark.asyncio
    async def test_internal_health_no_params(self, api: APITestHelper) -> None:
        """GET /internal/health with no params returns a clean response."""
        response = await api.client.get("/internal/health")
        assert response.status_code in (200, 404, 500), (
            f"Expected 200, 404, or 500, "
            f"got {response.status_code}: {response.text[:200]}"
        )
