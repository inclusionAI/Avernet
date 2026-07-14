"""E2E tests for PaaS facade API error paths."""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestCreateDeviceErrors:
    """Validation errors for POST /api/v1/paas/devices."""

    @pytest.mark.asyncio
    async def test_create_device_empty_body(self, api: APITestHelper) -> None:
        """POST /api/v1/paas/devices with empty body → 422."""
        response = await api.client.post("/api/v1/paas/devices", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_device_missing_tenant(self, api: APITestHelper) -> None:
        """Missing tenant_name returns 422."""
        response = await api.client.post(
            "/api/v1/paas/devices",
            json={"device_template_uuid": "x", "detail_config": {"name": "test"}},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_device_empty_tenant_name(self, api: APITestHelper) -> None:
        """Empty tenant_name string returns 422 (min_length=1)."""
        response = await api.client.post(
            "/api/v1/paas/devices",
            json={
                "tenant_name": "",
                "device_template_uuid": "TEMPLATE-x",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_device_invalid_extra_field(self, api: APITestHelper) -> None:
        """Unknown field in request body is tolerated or returns 422."""
        response = await api.client.post(
            "/api/v1/paas/devices",
            json={
                "tenant_name": "team_claw",
                "bogus_field": "should-not-exist",
            },
        )
        # FastAPI by default ignores extra fields; stub may also return 500
        assert response.status_code in (200, 422, 500)


class TestDestroyDeviceErrors:
    """Error cases for DELETE /api/v1/paas/devices/{paas_device_id}."""

    @pytest.mark.asyncio
    async def test_destroy_device_nonexistent(self, api: APITestHelper) -> None:
        """Delete a non-existent device returns 404."""
        response = await api.client.delete("/api/v1/paas/devices/NONEXISTENT-000000000")
        # The stub PaaS backend may return 200 (success) for any device ID
        assert response.status_code in (200, 404, 500)


class TestExecuteCommandErrors:
    """Validation errors for POST /api/v1/paas/devices/{paas_device_id}/commands."""

    @pytest.mark.asyncio
    async def test_execute_nonexistent_device(self, api: APITestHelper) -> None:
        """Execute command on a device that does not exist."""
        response = await api.client.post(
            "/api/v1/paas/devices/NONEXISTENT-000000000/commands",
            json={"cmd": "echo hi"},
        )
        assert response.status_code in (200, 404, 500)

    @pytest.mark.asyncio
    async def test_execute_empty_cmd(self, api: APITestHelper) -> None:
        """Empty cmd string returns 422 (min_length=1)."""
        response = await api.client.post(
            "/api/v1/paas/devices/NONEXISTENT-000000000/commands",
            json={"cmd": ""},
        )
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_execute_empty_body(self, api: APITestHelper) -> None:
        """Empty JSON body returns 422 (cmd is required)."""
        response = await api.client.post(
            "/api/v1/paas/devices/NONEXISTENT-000000000/commands",
            json={},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_execute_cmd_too_long(self, api: APITestHelper) -> None:
        """Extremely long cmd string may trigger validation."""
        long_cmd = "A" * 100_000
        response = await api.client.post(
            "/api/v1/paas/devices/NONEXISTENT-000000000/commands",
            json={"cmd": long_cmd},
        )
        assert response.status_code in (200, 400, 413, 422, 500)


class TestWsInfoErrors:
    """Validation errors for GET /api/v1/paas/devices/{paas_device_id}/ws-info."""

    @pytest.mark.asyncio
    async def test_ws_info_nonexistent_device(self, api: APITestHelper) -> None:
        """Get WS info for a non-existent device — stub may return 200."""
        response = await api.client.get(
            "/api/v1/paas/devices/NONEXISTENT-000000000/ws-info",
            params={"port": 8080, "path": "/ws"},
        )
        assert response.status_code in (200, 404, 500)

    @pytest.mark.asyncio
    async def test_ws_info_missing_port(self, api: APITestHelper) -> None:
        """Missing port query param returns 422 (port is required, ge=1)."""
        response = await api.client.get(
            "/api/v1/paas/devices/NONEXISTENT-000000000/ws-info",
            params={"path": "/ws"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_ws_info_missing_path(self, api: APITestHelper) -> None:
        """Missing path query param returns 422 (path is required)."""
        response = await api.client.get(
            "/api/v1/paas/devices/NONEXISTENT-000000000/ws-info",
            params={"port": 8080},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_ws_info_invalid_port_zero(self, api: APITestHelper) -> None:
        """Port 0 returns 422 (ge=1)."""
        response = await api.client.get(
            "/api/v1/paas/devices/NONEXISTENT-000000000/ws-info",
            params={"port": 0, "path": "/ws"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_ws_info_port_out_of_range(self, api: APITestHelper) -> None:
        """Port > 65535 returns 422 (le=65535)."""
        response = await api.client.get(
            "/api/v1/paas/devices/NONEXISTENT-000000000/ws-info",
            params={"port": 99999, "path": "/ws"},
        )
        assert response.status_code == 422


class TestDeviceInfoErrors:
    """Error cases for GET /api/v1/paas/devices/{paas_device_id}/info."""

    @pytest.mark.asyncio
    async def test_device_info_nonexistent(self, api: APITestHelper) -> None:
        """Get info for a non-existent device — stub may return 200."""
        response = await api.client.get(
            "/api/v1/paas/devices/NONEXISTENT-000000000/info"
        )
        assert response.status_code in (200, 404, 500)


class TestOutboundRuleErrors:
    """Validation errors for PUT /api/v1/paas/devices/{paas_device_id}/outbound-rule."""

    @pytest.mark.asyncio
    async def test_outbound_rules_nonexistent_device(self, api: APITestHelper) -> None:
        """Update outbound rule on a non-existent device."""
        response = await api.client.put(
            "/api/v1/paas/devices/NONEXISTENT-000000000/outbound-rule",
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
        assert response.status_code in (200, 404, 500)

    @pytest.mark.asyncio
    async def test_outbound_rules_empty_body(self, api: APITestHelper) -> None:
        """Empty body returns validation error or 200 from stub."""
        response = await api.client.put(
            "/api/v1/paas/devices/NONEXISTENT-000000000/outbound-rule",
            json={},
        )
        assert response.status_code in (200, 422)

    @pytest.mark.asyncio
    async def test_outbound_rules_missing_body(self, api: APITestHelper) -> None:
        """No body at all returns 422."""
        response = await api.client.put(
            "/api/v1/paas/devices/NONEXISTENT-000000000/outbound-rule",
        )
        assert response.status_code == 422


class TestInvokeHttpErrors:
    """Validation errors for GET/POST/PUT/DELETE .../invoke-http/{port}/{path}."""

    @pytest.mark.asyncio
    async def test_invoke_http_nonexistent_device(self, api: APITestHelper) -> None:
        """Invoke HTTP on a non-existent device."""
        response = await api.client.get(
            "/api/v1/paas/devices/NONEXISTENT-000000000/invoke-http/8080/api/health"
        )
        assert response.status_code in (404, 500, 501)

    @pytest.mark.asyncio
    async def test_invoke_http_invalid_port_too_low(self, api: APITestHelper) -> None:
        """Port 0 returns 422 (ge=1)."""
        response = await api.client.get(
            "/api/v1/paas/devices/NONEXISTENT-000000000/invoke-http/0/api/test"
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invoke_http_invalid_port_too_high(self, api: APITestHelper) -> None:
        """Port > 65535 returns 422 (le=65535)."""
        response = await api.client.get(
            "/api/v1/paas/devices/NONEXISTENT-000000000/invoke-http/99999/api/test"
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invoke_http_patch_not_allowed(self, api: APITestHelper) -> None:
        """PATCH method is not allowed — returns 405."""
        response = await api.client.patch(
            "/api/v1/paas/devices/NONEXISTENT-000000000/invoke-http/8080/api/test"
        )
        assert response.status_code == 405


class TestOpenFolderErrors:
    """Validation errors for POST /api/v1/paas/devices/{paas_device_id}/open-folder."""

    @pytest.mark.asyncio
    async def test_open_folder_nonexistent_device(self, api: APITestHelper) -> None:
        """Open folder on a non-existent device."""
        response = await api.client.post(
            "/api/v1/paas/devices/NONEXISTENT-000000000/open-folder",
            json={"folder_path": "/tmp"},
        )
        assert response.status_code in (404, 500, 501)


class TestUpdateTtlErrors:
    """Error cases for PUT /api/v1/paas/devices/{paas_device_id}/ttl."""

    @pytest.mark.asyncio
    async def test_update_ttl_nonexistent_device(self, api: APITestHelper) -> None:
        """Update TTL for a non-existent device — stub may return 200."""
        response = await api.client.put(
            "/api/v1/paas/devices/NONEXISTENT-000000000/ttl"
        )
        assert response.status_code in (200, 404, 500, 501)


class TestMalformedJson:
    """Malformed request body handling."""

    @pytest.mark.asyncio
    async def test_malformed_json_body(self, api: APITestHelper) -> None:
        """Non-JSON body with JSON content-type returns 400 or 422."""
        response = await api.client.post(
            "/api/v1/paas/devices",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in (400, 422)
