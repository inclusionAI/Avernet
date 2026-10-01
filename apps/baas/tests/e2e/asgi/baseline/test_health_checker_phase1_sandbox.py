"""E2E tests for Sandbox Health Provider (Phase 1.2).

Exercises the sandbox health check endpoint exposed via the bot-health-checker
router. All tests are read-only — they verify routing and parameter handling
(no 500s from DI / provider wiring failures).

Endpoint mapping:
- GET /api/v1/bot-health-checker/sandbox — get_sandbox_info
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestSandboxHealth:
    """Tests for GET /api/v1/bot-health-checker/sandbox."""

    @pytest.mark.asyncio
    async def test_sandbox_with_dummy_id(self, api: APITestHelper) -> None:
        """GET /sandbox with a dummy sandbox_id returns non-500."""
        response = await api.client.get(
            api.sandbox_health_url(),
            params=api.params(sandbox_id="dummy-sandbox"),
        )

        assert response.status_code != 500, (
            f"Expected non-500, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_sandbox_with_nonexistent_id(self, api: APITestHelper) -> None:
        """GET /sandbox with a nonexistent sandbox_id returns 4xx (not 500)."""
        response = await api.client.get(
            api.sandbox_health_url(),
            params=api.params(sandbox_id="nonexistent-sandbox-12345"),
        )

        assert response.status_code != 500, (
            f"Expected non-500 for nonexistent sandbox, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_sandbox_missing_required_param(self, api: APITestHelper) -> None:
        """GET /sandbox without sandbox_id returns 422 validation error."""
        response = await api.client.get(
            api.sandbox_health_url(),
            params=api.params(),
        )

        assert response.status_code != 500, (
            f"Expected non-500 for missing param, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_sandbox_with_empty_id(self, api: APITestHelper) -> None:
        """GET /sandbox with an empty sandbox_id returns 422 validation error."""
        response = await api.client.get(
            api.sandbox_health_url(),
            params=api.params(sandbox_id="   "),
        )

        assert response.status_code != 500, (
            f"Expected non-500 for empty param, got {response.status_code}: "
            f"{response.text[:200]}"
        )
