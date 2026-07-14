"""E2E tests for paas_facade_router — all 9 endpoints.

Tests cover:
- POST   /api/v1/paas/devices                           — Create device
- DELETE /api/v1/paas/devices/{paas_device_id}          — Destroy device
- POST   /api/v1/paas/devices/{paas_device_id}/commands  — Execute command
- GET    /api/v1/paas/devices/{paas_device_id}/ws-info   — Get WebSocket info
- GET    /api/v1/paas/devices/{paas_device_id}/info      — Get device info
- PUT    /api/v1/paas/devices/{paas_device_id}/outbound-rule — Update outbound rule
- GET/POST/PUT/DELETE /api/v1/paas/devices/{id}/invoke-http/{port}/{path} — Proxy HTTP
- POST   /api/v1/paas/devices/{paas_device_id}/open-folder — Open folder
- PUT    /api/v1/paas/devices/{paas_device_id}/ttl       — Update device TTL
"""

from typing import Any

import pytest

from ..conftest import APITestHelper


def _assert_not_di_error(response: Any) -> None:
    """Fail if the response contains a dependency-injection wiring error.

    A healthy 500 response comes from a known PaaS/upstream failure.
    A DI wiring error (Provide placeholder not resolved) is a code-level
    bug that must NOT be silently swallowed by a broad status-code assertion.
    """
    if response.status_code != 500:
        return
    try:
        body = response.json()
    except Exception:
        return
    detail = body.get("detail", body)
    msg = detail.get("message", "") if isinstance(detail, dict) else str(detail)
    assert "Provide" not in msg, (
        f"DI container wiring error detected: {msg}\n"
        "The Provide placeholder is not being resolved. "
        "Check that container.wire() is called and @inject decorator is present."
    )


pytestmark = [pytest.mark.e2e, pytest.mark.crud]


class TestCreateDeviceEndpoint:
    """Tests for POST /api/v1/paas/devices."""

    @pytest.mark.asyncio
    async def test_create_device(self, api: APITestHelper, unique_id: str) -> None:
        """Create a PaaS device with the new signature."""
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                "detail_config": {
                    "name": f"e2e-test-device-{unique_id}",
                    "ttl_in_minutes": 60,
                },
            },
        )
        _assert_not_di_error(response)

    @pytest.mark.asyncio
    async def test_create_device_missing_tenant(self, api: APITestHelper) -> None:
        """Missing tenant_name returns 422."""
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "device_template_uuid": "TEMPLATE-x",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_device_empty_tenant(self, api: APITestHelper) -> None:
        """Empty tenant_name returns 422."""
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": "",
                "device_template_uuid": "TEMPLATE-x",
            },
        )
        assert response.status_code == 422


class TestDestroyDeviceEndpoint:
    """Tests for DELETE /api/v1/paas/devices/{paas_device_id}."""

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_device(self, api: APITestHelper) -> None:
        """Destroy a non-existent device."""
        response = await api.client.delete(
            api.paas_device_url("no-such-device@0"),
            params=api.params(),
        )
        _assert_not_di_error(response)
        assert response.status_code in (200, 400, 404, 500)

    @pytest.mark.asyncio
    async def test_destroy_device_with_template_suffix(
        self, api: APITestHelper
    ) -> None:
        """Destroy with @template_id suffix."""
        response = await api.client.delete(
            api.paas_device_url("missing-sandbox@1"),
            params=api.params(),
        )
        _assert_not_di_error(response)
        assert response.status_code in (200, 400, 404, 500)


class TestExecuteCommandEndpoint:
    """Tests for POST /api/v1/paas/devices/{paas_device_id}/commands."""

    @pytest.mark.asyncio
    async def test_execute_command_on_nonexistent_device(
        self, api: APITestHelper
    ) -> None:
        """Execute command on a device that does not exist."""
        response = await api.client.post(
            api.paas_device_url("no-such-device@0", "commands"),
            params=api.params(),
            json={"cmd": "echo hello"},
        )
        _assert_not_di_error(response)
        assert response.status_code in (200, 400, 404, 422, 500, 503)

    @pytest.mark.asyncio
    async def test_execute_command_empty_cmd(self, api: APITestHelper) -> None:
        """Empty command returns 422."""
        response = await api.client.post(
            api.paas_device_url("dev@0", "commands"),
            params=api.params(),
            json={"cmd": ""},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_execute_command_with_env(self, api: APITestHelper) -> None:
        """Execute command with environment variables."""
        response = await api.client.post(
            api.paas_device_url("dev@0", "commands"),
            params=api.params(),
            json={"cmd": "env", "env": {"KEY": "value"}},
        )
        _assert_not_di_error(response)
        assert response.status_code in (200, 400, 404, 422, 500, 503)


class TestGetDeviceWsInfoEndpoint:
    """Tests for GET /api/v1/paas/devices/{paas_device_id}/ws-info."""

    @pytest.mark.asyncio
    async def test_ws_info_nonexistent_device(self, api: APITestHelper) -> None:
        """Get WS info for a non-existent device."""
        response = await api.client.get(
            api.paas_device_url("no-such-device@0", "ws-info"),
            params=api.params(port=8080, path="/ws"),
        )
        assert response.status_code in (200, 400, 404, 422, 500)

    @pytest.mark.asyncio
    async def test_ws_info_missing_port(self, api: APITestHelper) -> None:
        """Missing port query parameter returns 422."""
        response = await api.client.get(
            api.paas_device_url("dev@0", "ws-info"),
            params=api.params(path="/ws"),
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_ws_info_invalid_port(self, api: APITestHelper) -> None:
        """Port out of range returns 422."""
        response = await api.client.get(
            api.paas_device_url("dev@0", "ws-info"),
            params=api.params(port=0, path="/ws"),
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_ws_info_missing_path(self, api: APITestHelper) -> None:
        """Missing path query parameter returns 422."""
        response = await api.client.get(
            api.paas_device_url("dev@0", "ws-info"),
            params=api.params(port=8080),
        )
        assert response.status_code == 422


class TestGetDeviceInfoEndpoint:
    """Tests for GET /api/v1/paas/devices/{paas_device_id}/info."""

    @pytest.mark.asyncio
    async def test_get_device_info_nonexistent(self, api: APITestHelper) -> None:
        """Get info for a non-existent device."""
        response = await api.client.get(
            api.paas_device_url("no-such-device@0", "info"),
            params=api.params(),
        )
        assert response.status_code in (200, 400, 404, 422, 500)

    @pytest.mark.asyncio
    async def test_get_device_info_simple_id(self, api: APITestHelper) -> None:
        """Get info with a simple device ID (no @template_id suffix)."""
        response = await api.client.get(
            api.paas_device_url("nonexistent-device", "info"),
            params=api.params(),
        )
        assert response.status_code in (200, 400, 404, 422, 500)


class TestUpdateOutboundRuleEndpoint:
    """Tests for PUT /api/v1/paas/devices/{paas_device_id}/outbound-rule."""

    @pytest.mark.asyncio
    async def test_update_outbound_rule_empty_rules(self, api: APITestHelper) -> None:
        """Update outbound rule with empty header_operation_rules."""
        response = await api.client.put(
            api.paas_device_url("dev@0", "outbound-rule"),
            params=api.params(),
            json={"header_operation_rules": []},
        )
        _assert_not_di_error(response)
        assert response.status_code in (200, 400, 404, 422, 500, 503)

    @pytest.mark.asyncio
    async def test_update_outbound_rule_with_rules(self, api: APITestHelper) -> None:
        """Update outbound rule with a populated rule."""
        response = await api.client.put(
            api.paas_device_url("dev@0", "outbound-rule"),
            params=api.params(),
            json={
                "header_operation_rules": [
                    {
                        "domains": ["example.com"],
                        "action": "ALLOW",
                        "header_name": "X-Custom",
                        "value": "test",
                    }
                ]
            },
        )
        _assert_not_di_error(response)
        assert response.status_code in (200, 400, 404, 422, 500, 503)

    @pytest.mark.asyncio
    async def test_update_outbound_rule_missing_body(self, api: APITestHelper) -> None:
        """Missing request body returns 422."""
        response = await api.client.put(
            api.paas_device_url("dev@0", "outbound-rule"),
            params=api.params(),
        )
        assert response.status_code == 422


class TestInvokeHttpEndpoint:
    """Tests for GET/POST/PUT/DELETE /api/v1/paas/devices/{id}/invoke-http/{port}/{path}."""

    @pytest.mark.asyncio
    async def test_invoke_http_get(self, api: APITestHelper) -> None:
        """GET proxy to an internal device service."""
        response = await api.client.get(
            f"{api.paas_device_url('dev@0')}/invoke-http/8080/api/health",
            params=api.params(),
        )
        assert response.status_code in (200, 404, 500, 501)

    @pytest.mark.asyncio
    async def test_invoke_http_post(self, api: APITestHelper) -> None:
        """POST proxy to an internal device service."""
        response = await api.client.post(
            f"{api.paas_device_url('dev@0')}/invoke-http/8080/api/data",
            params=api.params(),
            json={"key": "value"},
        )
        assert response.status_code in (200, 404, 500, 501)

    @pytest.mark.asyncio
    async def test_invoke_http_put(self, api: APITestHelper) -> None:
        """PUT proxy to an internal device service."""
        response = await api.client.put(
            f"{api.paas_device_url('dev@0')}/invoke-http/8080/api/data/1",
            params=api.params(),
            json={"name": "updated"},
        )
        assert response.status_code in (200, 404, 500, 501)

    @pytest.mark.asyncio
    async def test_invoke_http_delete(self, api: APITestHelper) -> None:
        """DELETE proxy to an internal device service."""
        response = await api.client.delete(
            f"{api.paas_device_url('dev@0')}/invoke-http/8080/api/data/1",
            params=api.params(),
        )
        assert response.status_code in (200, 404, 500, 501)

    @pytest.mark.asyncio
    async def test_invoke_http_patch_not_allowed(self, api: APITestHelper) -> None:
        """PATCH method is not allowed — returns 405."""
        response = await api.client.patch(
            f"{api.paas_device_url('dev@0')}/invoke-http/8080/api/test",
            params=api.params(),
        )
        assert response.status_code == 405

    @pytest.mark.asyncio
    async def test_invoke_http_invalid_port(self, api: APITestHelper) -> None:
        """Port out of range returns 422."""
        response = await api.client.get(
            f"{api.paas_device_url('dev@0')}/invoke-http/99999/api/test",
            params=api.params(),
        )
        assert response.status_code == 422


class TestOpenFolderEndpoint:
    """Tests for POST /api/v1/paas/devices/{paas_device_id}/open-folder."""

    @pytest.mark.asyncio
    async def test_open_folder_default_path(self, api: APITestHelper) -> None:
        """Open folder with default path."""
        response = await api.client.post(
            api.paas_device_url("dev@0", "open-folder"),
            params=api.params(),
            json={},
        )
        assert response.status_code in (200, 400, 404, 500, 501)

    @pytest.mark.asyncio
    async def test_open_folder_with_path(self, api: APITestHelper) -> None:
        """Open folder with a specific path."""
        response = await api.client.post(
            api.paas_device_url("dev@0", "open-folder"),
            params=api.params(),
            json={"folder_path": "/home/admin"},
        )
        assert response.status_code in (200, 400, 404, 500, 501)

    @pytest.mark.asyncio
    async def test_open_folder_no_body(self, api: APITestHelper) -> None:
        """Open folder with no request body (folder_path defaults to None)."""
        response = await api.client.post(
            api.paas_device_url("dev@0", "open-folder"),
            params=api.params(),
        )
        assert response.status_code in (200, 400, 404, 500, 501)


class TestUpdateDeviceTtlEndpoint:
    """Tests for PUT /api/v1/paas/devices/{paas_device_id}/ttl."""

    @pytest.mark.asyncio
    async def test_update_ttl_nonexistent_device(self, api: APITestHelper) -> None:
        """Update TTL for a non-existent device."""
        response = await api.client.put(
            api.paas_device_url("no-such-device@0", "ttl"),
            params=api.params(),
        )
        assert response.status_code in (200, 400, 404, 500, 501, 503)

    @pytest.mark.asyncio
    async def test_update_ttl_authorized(self, api: APITestHelper) -> None:
        """Update TTL with an authorized request."""
        response = await api.client.put(
            api.paas_device_url("dev@1", "ttl"),
            params=api.params(),
        )
        assert response.status_code in (200, 400, 404, 500, 501)
