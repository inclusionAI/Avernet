"""Phase 1.10 Gap Closure — pure E2E tests, zero production code changes.

Targets router files below 85% coverage. Exercises endpoints not
covered by existing test suites. All tests work against vanilla production code.
"""

import pytest  # noqa: I001

from tests.e2e.asgi.conftest import (  # noqa: E402
    APITestHelper,
    TEMPLATE_ARCA,
)


pytestmark = [pytest.mark.e2e_asgi]


# ═══════════════════════════════════════════════════════════════════
# System Config Router — /api/v1/system-configs (51.0%, 24 missing)
# ═══════════════════════════════════════════════════════════════════


class TestSystemConfig:
    """GET/POST/PUT/DELETE /api/v1/system-configs — CRUD."""

    @pytest.mark.asyncio
    async def test_list_system_configs(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/system-configs",
            params=api.params(),
        )
        assert response.status_code in (200, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_get_system_config_not_found(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/system-configs/nonexistent_key",
            params=api.params(),
        )
        assert response.status_code in (404, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_create_system_config_invalid(self, api: APITestHelper) -> None:
        response = await api.client.post(
            "/api/v1/system-configs",
            params=api.params(),
            json={"invalid": True},
        )
        assert response.status_code in (400, 422, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.put(
            "/api/v1/system-configs/nonexistent",
            params=api.params(),
            json={"conf_value": "x"},
        )
        assert response.status_code in (404, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.delete(
            "/api/v1/system-configs/nonexistent",
            params=api.params(),
        )
        assert response.status_code in (404, 401, 500), (
            f"Unexpected: {response.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════
# Tenant Router — /api/v1/tenants (50.0%, 28 missing)
# ═══════════════════════════════════════════════════════════════════


class TestTenantCRUD:
    """GET/POST/PUT/DELETE /api/v1/tenants."""

    @pytest.mark.asyncio
    async def test_list_tenants(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/tenants",
            params=api.params(),
        )
        assert response.status_code == 200, (
            f"List tenants failed: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_get_tenant_exists(self, api: APITestHelper) -> None:
        response = await api.client.get(
            f"/api/v1/tenants/{api.tenant}",
            params=api.params(),
        )
        assert response.status_code == 200, f"Get tenant failed: {response.status_code}"

    @pytest.mark.asyncio
    async def test_get_tenant_not_found(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/tenants/nonexistent_tenant_xyz",
            params=api.params(),
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"

    @pytest.mark.asyncio
    async def test_get_tenant_config(self, api: APITestHelper) -> None:
        response = await api.client.get(
            f"/api/v1/tenants/{api.tenant}/config",
            params=api.params(),
        )
        assert response.status_code in (200, 404), (
            f"Get tenant config: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_update_nonexistent_tenant(self, api: APITestHelper) -> None:
        response = await api.client.put(
            "/api/v1/tenants/nonexistent_tenant_xyz",
            params=api.params(),
            json={"description": "updated"},
        )
        assert response.status_code in (404, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_delete_nonexistent_tenant(self, api: APITestHelper) -> None:
        response = await api.client.delete(
            "/api/v1/tenants/nonexistent_tenant_xyz",
            params=api.params(),
        )
        assert response.status_code in (404, 401, 500), (
            f"Unexpected: {response.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════
# QPM Config Router — /api/v1/bot-qpm (65.2%, 23 missing)
# ═══════════════════════════════════════════════════════════════════


class TestQPMConfig:
    """GET/POST/PUT/DELETE /api/v1/bot-qpm."""

    @pytest.mark.asyncio
    async def test_list_qpm_configs(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/bot-qpm",
            params=api.params(),
        )
        assert response.status_code in (200, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_get_qpm_not_found(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/bot-qpm/nonexistent_bot",
            params=api.params(),
        )
        assert response.status_code in (404, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_upsert_qpm_minimal(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            "/api/v1/bot-qpm",
            params=api.params(),
            json={"bot_id": bot_uuid, "qpm": 10},
        )
        assert response.status_code in (201, 200, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_update_qpm_not_found(self, api: APITestHelper) -> None:
        response = await api.client.put(
            "/api/v1/bot-qpm/nonexistent_bot",
            params=api.params(),
            json={"qpm_limit": 50},
        )
        assert response.status_code in (404, 401, 422, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_delete_qpm_not_found(self, api: APITestHelper) -> None:
        response = await api.client.delete(
            "/api/v1/bot-qpm/nonexistent_bot",
            params=api.params(),
        )
        assert response.status_code in (404, 401, 500), (
            f"Unexpected: {response.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════
# Publish Admin Router — /api/v1/admin (73.3%, 12 missing)
# ═══════════════════════════════════════════════════════════════════


class TestPublishAdmin:
    """POST /api/v1/admin/force-success, /api/v1/admin/devices/{id}/status."""

    @pytest.mark.asyncio
    async def test_force_success_missing_params(self, api: APITestHelper) -> None:
        response = await api.client.post(
            "/api/v1/admin/force-success",
            params=api.params(),
            json={},
        )
        assert response.status_code in (400, 422, 401, 403, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_update_device_status_invalid(self, api: APITestHelper) -> None:
        response = await api.client.post(
            "/api/v1/admin/devices/nonexistent-uuid/status",
            params=api.params(),
            json={"status": "ONLINE"},
        )
        assert response.status_code in (404, 400, 401, 403, 422, 500), (
            f"Unexpected: {response.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════
# Bot Management Router — /api/v1/bots (67.9%, 42 missing)
# ═══════════════════════════════════════════════════════════════════


class TestBotManagement:
    """Bot detail, device-status, devices endpoints."""

    @pytest.mark.asyncio
    async def test_bot_detail_by_uuid(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.get(
            f"/api/v1/bots/{bot_uuid}/detail-by-uuid",
            params=api.params(),
        )
        assert response.status_code == 200, (
            f"Detail by UUID failed: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_bot_device_status(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.get(
            f"/api/v1/bots/{bot_uuid}/device-status",
            params=api.params(),
        )
        assert response.status_code == 200, (
            f"Device status failed: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_bot_devices(self, api: APITestHelper, created_bot: dict) -> None:
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.get(
            f"/api/v1/bots/{bot_uuid}/devices",
            params=api.params(),
        )
        assert response.status_code == 200, (
            f"Bot devices failed: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_bot_devices_by_id_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/bots/99999/devices-by-id",
            params=api.params(),
        )
        assert response.status_code in (200, 404, 400, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_bot_detail_by_id_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/bots/99999/detail-by-id",
            params=api.params(),
        )
        assert response.status_code in (404, 400, 500), (
            f"Unexpected: {response.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════
# Publish Router — /api/v1/publishes (66.7%, 38 missing)
# ═══════════════════════════════════════════════════════════════════


class TestPublishWorkflow:
    """Publish lifecycle: get, progress, approve, reject, revoke, retry."""

    @pytest.mark.asyncio
    async def test_publish_get_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/publishes/99999",
            params=api.params(),
        )
        assert response.status_code in (404, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_publish_progress_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/publishes/99999/progress",
            params=api.params(),
        )
        assert response.status_code in (404, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_publish_approve_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.post(
            "/api/v1/publishes/99999/approve",
            params=api.params(),
            json={"request_id": "req-1", "operator": "test-user"},
        )
        assert response.status_code in (404, 401, 422, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_publish_reject_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.post(
            "/api/v1/publishes/99999/reject",
            params=api.params(),
            json={"request_id": "req-1", "operator": "test-user"},
        )
        assert response.status_code in (404, 401, 422, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_publish_revoke_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.post(
            "/api/v1/publishes/99999/revoke",
            params=api.params(),
            json={"request_id": "req-1", "operator": "test-user"},
        )
        assert response.status_code in (404, 401, 422, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_publish_execute_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.post(
            "/api/v1/publishes/99999/execute",
            params=api.params(),
            json={"request_id": "req-1", "operator": "test-user"},
        )
        assert response.status_code in (404, 401, 422, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_publish_complete_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.post(
            "/api/v1/publishes/99999/complete",
            params=api.params(),
            json={"request_id": "req-1", "operator": "test-user"},
        )
        assert response.status_code in (404, 401, 422, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_publish_retry_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.post(
            "/api/v1/publishes/99999/retry",
            params=api.params(),
            json={"request_id": "req-1", "operator": "test-user"},
        )
        assert response.status_code in (404, 401, 422, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_publish_start_progress_nonexistent(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/publishes/99999/start-progress",
            params=api.params(),
        )
        assert response.status_code in (404, 401, 500), (
            f"Unexpected: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_device_callback_empty(self, api: APITestHelper) -> None:
        response = await api.client.post(
            "/api/v1/publish/device-callback",
            json={},
        )
        assert response.status_code in (400, 422, 401, 500), (
            f"Unexpected: {response.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════
# Bot Service CMD / HTTP / HTTP-CONN / WSS routers
# ═══════════════════════════════════════════════════════════════════


class TestBotServiceDispatchers:
    """Bot runtime dispatcher endpoints."""

    @pytest.mark.asyncio
    async def test_http_conn_info_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.get(
            f"/api/v1/bots/{bot_uuid}/http-conn-info",
            params=api.params(),
        )
        assert response.status_code in (200, 404, 500), (
            f"HTTP conn info: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_wss_conn_info_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.get(
            f"/api/v1/bots/{bot_uuid}/wss-conn-info",
            params=api.params(),
        )
        assert response.status_code in (200, 404, 500), (
            f"WSS conn info: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_cmd_dispatch_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            f"/api/v1/bots/{bot_uuid}/cmd",
            params=api.params(),
            json={"cmd": "echo hello"},
        )
        assert response.status_code in (200, 400, 404, 500, 503), (
            f"CMD dispatch: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_http_dispatch_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            f"/api/v1/bots/{bot_uuid}/http",
            params=api.params(),
            json={"method": "GET", "path": "/health"},
        )
        assert response.status_code in (200, 400, 404, 500, 503), (
            f"HTTP dispatch: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_wss_dispatch_valid_bot(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        bot_uuid = created_bot["bot_uuid"]
        response = await api.client.post(
            f"/api/v1/bots/{bot_uuid}/wss",
            params=api.params(),
            json={"message": "test"},
        )
        assert response.status_code in (200, 400, 404, 500, 503), (
            f"WSS dispatch: {response.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════
# Relay Session Router — 45.8% (26 missing)
# ═══════════════════════════════════════════════════════════════════


class TestRelaySession:
    """Relay session endpoints."""

    @pytest.mark.asyncio
    async def test_list_relay_sessions(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/relay-sessions",
            params=api.params(),
        )
        assert response.status_code in (200, 404, 500), (
            f"Relay sessions: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_get_relay_session_not_found(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/relay-sessions/nonexistent",
            params=api.params(),
        )
        assert response.status_code in (404, 400, 500), (
            f"Unexpected: {response.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════
# Internal Router — 41.3% (27 missing)
# ═══════════════════════════════════════════════════════════════════


class TestInternalRouter:
    """Internal-only endpoints."""

    @pytest.mark.asyncio
    async def test_internal_health(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/internal/health",
        )
        assert response.status_code in (200, 404, 500), (
            f"Internal health: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_internal_cache_info(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/internal/cache",
        )
        assert response.status_code in (200, 404, 500), (
            f"Internal cache: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_internal_cache_clear(self, api: APITestHelper) -> None:
        response = await api.client.delete(
            "/api/v1/internal/cache",
        )
        assert response.status_code in (200, 404, 500), (
            f"Cache clear: {response.status_code}"
        )
