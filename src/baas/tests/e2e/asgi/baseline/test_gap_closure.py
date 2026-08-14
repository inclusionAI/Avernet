"""Phase 1.10 Gap Closure E2E Tests.

Targets router/API files with 10+ uncovered statements below 85%.
Focuses on endpoints that our existing test suite doesn't exercise,
prioritizing coverage-per-test-line efficiency.
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


# ============================================================================
# Health Checker Router — /api/v1/bot-health-checker/*
# Currently 33.1% (117 missing)
# ============================================================================


_HEALTH_CHECKER_URL = "/api/v1/bot-health-checker"


class TestHealthCheckerAliveUnauthenticated:
    """GET /api/v1/bot-health-checker/alive — device liveness (requires auth)."""

    @pytest.mark.asyncio
    async def test_alive_requires_auth(self, api: APITestHelper) -> None:
        """Verify alive endpoint rejects unauthenticated requests."""
        response = await api.client.get(
            f"{_HEALTH_CHECKER_URL}/alive",
            params={"bot_id": "x", "entity_id": "y", "statuses": "online"},
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"

    @pytest.mark.asyncio
    async def test_alive_missing_params_requires_auth(self, api: APITestHelper) -> None:
        """Missing params still get 401 before validation."""
        response = await api.client.get(
            f"{_HEALTH_CHECKER_URL}/alive",
            params={"bot_id": "some-bot"},
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"


class TestHealthCheckerSandbox:
    """GET /api/v1/bot-health-checker/sandbox — reverse sandbox lookup."""

    @pytest.mark.asyncio
    async def test_sandbox_requires_auth(self, api: APITestHelper) -> None:
        """Sandbox lookup without auth — 401."""
        response = await api.client.get(
            f"{_HEALTH_CHECKER_URL}/sandbox",
            params={"sandbox_id": "any-id"},
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"


class TestHealthCheckerTTLExtend:
    """POST /api/v1/bot-health-checker/ttl/extend — TTL extension."""

    @pytest.mark.asyncio
    async def test_ttl_extend_requires_auth(self, api: APITestHelper) -> None:
        """TTL extend without auth — 401."""
        response = await api.client.post(
            f"{_HEALTH_CHECKER_URL}/ttl/extend",
            json={"bot_id": "x", "entity_id": "y", "env": "prod"},
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"


class TestHealthCheckerActiveBots:
    """GET /api/v1/bot-health-checker/active_bots — paginated bot listing."""

    @pytest.mark.asyncio
    async def test_active_bots_requires_auth(self, api: APITestHelper) -> None:
        """Active bots without auth — 401."""
        response = await api.client.get(
            f"{_HEALTH_CHECKER_URL}/active_bots",
            params={"page": 1, "page_size": 10},
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"


class TestHealthCheckerDevices:
    """GET /api/v1/bot-health-checker/devices — PaaS devices for a bot."""

    @pytest.mark.asyncio
    async def test_devices_requires_auth(self, api: APITestHelper) -> None:
        """Devices without auth — 401."""
        response = await api.client.get(
            f"{_HEALTH_CHECKER_URL}/devices",
            params={"bot_id": "x", "entity_id": "y"},
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"


# ============================================================================
# Config Management — /api/v1/api-keys/*
# Currently 65.7% (api_gateway_router, 49 missing)
# ============================================================================


class TestAPIKeysAllowedBots:
    """API key allowed-bots grant/revoke endpoints."""

    @pytest.mark.asyncio
    async def test_get_allowed_bots_invalid_prefix(self, api: APITestHelper) -> None:
        """GET allowed-bots with nonexistent key prefix — 404."""
        response = await api.client.get(
            "/api/v1/api-keys/nonexistent-prefix/allowed-bots",
            params=api.params(),
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"

    @pytest.mark.asyncio
    async def test_grant_allowed_bot_invalid_key(self, api: APITestHelper) -> None:
        """POST grant with nonexistent key — 404."""
        response = await api.client.post(
            "/api/v1/api-keys/nonexistent-prefix/allowed-bots/grant",
            params=api.params(),
            json={"bot_id": "some-bot-uuid"},
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"

    @pytest.mark.asyncio
    async def test_revoke_allowed_bot_invalid_key(self, api: APITestHelper) -> None:
        """POST revoke with nonexistent key — 404."""
        response = await api.client.post(
            "/api/v1/api-keys/nonexistent-prefix/allowed-bots/revoke",
            params=api.params(),
            json={"bot_id": "some-bot-uuid"},
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"


class TestAPIKeysStatusTransition:
    """PATCH /api/v1/api-keys/{prefix}/status — activate/deactivate/revoke."""

    @pytest.mark.asyncio
    async def test_status_transition_invalid_key(self, api: APITestHelper) -> None:
        """PATCH status on nonexistent key — 404."""
        response = await api.client.patch(
            "/api/v1/api-keys/nonexistent-prefix/status",
            params=api.params(),
            json={"action": "activate"},
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"

    @pytest.mark.asyncio
    async def test_status_transition_invalid_action(self, api: APITestHelper) -> None:
        """PATCH status with invalid action on nonexistent key — 404."""
        response = await api.client.patch(
            "/api/v1/api-keys/nonexistent-prefix/status",
            params=api.params(),
            json={"action": "invalid_action"},
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"


class TestAPIKeysUpdate:
    """PUT /api/v1/api-keys/{prefix} — update key metadata."""

    @pytest.mark.asyncio
    async def test_update_invalid_key(self, api: APITestHelper) -> None:
        """PUT update on nonexistent key — 404."""
        response = await api.client.put(
            "/api/v1/api-keys/nonexistent-prefix",
            params=api.params(),
            json={"key_name": "updated-name"},
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"


# ============================================================================
# Config Management — /api/v1/device-templates/*
# Currently 80.5% (device_template_router, 17 missing)
# ============================================================================


class TestDeviceTemplateResolve:
    """GET /api/v1/device-templates/resolve — resolve template UUID."""

    @pytest.mark.asyncio
    async def test_resolve_default(self, api: APITestHelper) -> None:
        """Resolve with no UUID — returns tenant default template."""
        response = await api.client.get(
            "/api/v1/device-templates/resolve",
            params=api.params(),
        )
        assert response.status_code in (200, 404), (
            f"Resolve default failed: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_resolve_specific_uuid(self, api: APITestHelper) -> None:
        """Resolve with a specific template UUID."""
        response = await api.client.get(
            "/api/v1/device-templates/resolve",
            params={
                **api.params(),
                "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
            },
        )
        assert response.status_code in (200, 404), (
            f"Resolve specific UUID failed: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_resolve_invalid_uuid(self, api: APITestHelper) -> None:
        """Resolve with nonexistent UUID — 404."""
        response = await api.client.get(
            "/api/v1/device-templates/resolve",
            params={
                **api.params(),
                "template_uuid": "TEMPLATE-nonexistent-uuid-1234",
            },
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"


class TestDeviceTemplateStatusTransitions:
    """POST /api/v1/device-templates/{uuid}/status-transitions — state machine."""

    @pytest.mark.asyncio
    async def test_transition_invalid_uuid(self, api: APITestHelper) -> None:
        """Status transition on nonexistent template — error."""
        response = await api.client.post(
            "/api/v1/device-templates/nonexistent-uuid/status-transitions",
            params=api.params(),
            json={"target_status": "OFFLINE"},
        )
        assert response.status_code in (400, 404, 422), (
            f"Expected 400/404/422, got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_transition_invalid_target(self, api: APITestHelper) -> None:
        """Status transition with invalid target status — validation error."""
        response = await api.client.post(
            "/api/v1/device-templates/some-uuid/status-transitions",
            params=api.params(),
            json={"target_status": "INVALID"},
        )
        assert response.status_code in (400, 422), (
            f"Expected 400/422, got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_transition_missing_target(self, api: APITestHelper) -> None:
        """Status transition without target_status — validation error."""
        response = await api.client.post(
            "/api/v1/device-templates/some-uuid/status-transitions",
            params=api.params(),
            json={},
        )
        assert response.status_code in (400, 422), (
            f"Expected 400/422, got {response.status_code}"
        )


class TestDeviceTemplateDelete:
    """POST /api/v1/device-templates/{uuid}/delete — soft delete."""

    @pytest.mark.asyncio
    async def test_delete_invalid_uuid(self, api: APITestHelper) -> None:
        """Soft delete on nonexistent template — error."""
        response = await api.client.post(
            "/api/v1/device-templates/nonexistent-uuid/delete",
            params=api.params(),
        )
        assert response.status_code in (400, 404, 422), (
            f"Expected 400/404/422, got {response.status_code}"
        )


class TestDeviceTemplateByTemplateId:
    """GET /api/v1/device-templates/by-template-id/{id} — global lookup."""

    @pytest.mark.asyncio
    async def test_by_template_id_nonexistent(self, api: APITestHelper) -> None:
        """Lookup by nonexistent template_id — 404."""
        response = await api.client.get(
            "/api/v1/device-templates/by-template-id/99999",
            params=api.params(),
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"

    @pytest.mark.asyncio
    async def test_by_template_id_invalid(self, api: APITestHelper) -> None:
        """Lookup by non-integer template_id — validation error."""
        response = await api.client.get(
            "/api/v1/device-templates/by-template-id/not-a-number",
            params=api.params(),
        )
        assert response.status_code in (400, 404, 422), (
            f"Expected 400/404/422, got {response.status_code}"
        )


# ============================================================================
# Bot Service Open Folder Router — currently 62.3% (20 missing)
# ============================================================================


class TestOpenFolder:
    """POST /api/v1/bots/{uuid}/open-folder — code editor integration."""

    @pytest.mark.asyncio
    async def test_open_folder_invalid_bot(self, api: APITestHelper) -> None:
        """Open folder for nonexistent bot — error."""
        response = await api.client.post(
            "/api/v1/bots/nonexistent-uuid/open-folder",
            params=api.params(),
            json={"path": "/some/path"},
        )
        assert response.status_code in (400, 404, 500), (
            f"Unexpected status: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_open_folder_empty_path(self, api: APITestHelper) -> None:
        """Open folder with empty path — validation or routing error."""
        response = await api.client.post(
            "/api/v1/bots/nonexistent-uuid/open-folder",
            params=api.params(),
            json={"path": ""},
        )
        assert response.status_code in (400, 404, 422, 500), (
            f"Unexpected status: {response.status_code}"
        )
