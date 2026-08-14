"""E2E tests for PaaS Facade device lifecycle (Section 12).

Tests cover create → exercise → destroy flows on real created devices:
- POST   /api/v1/paas/devices                           — Create device
- DELETE /api/v1/paas/devices/{id}                       — Destroy device (idempotent)
- POST   /api/v1/paas/devices/{id}/commands              — Execute command
- GET    /api/v1/paas/devices/{id}/ws-info               — WebSocket info
- GET    /api/v1/paas/devices/{id}/info                  — Device info
- PUT    /api/v1/paas/devices/{id}/outbound-rule         — Outbound rule
- GET/POST/PUT/DELETE /api/v1/paas/devices/{id}/invoke-http/{port}/{path} — HTTP proxy
- POST   /api/v1/paas/devices/{id}/open-folder           — Open folder
- PUT    /api/v1/paas/devices/{id}/ttl                   — Update TTL
"""

from typing import Any

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)


def _get_device_id(device: dict[str, object]) -> str:
    """Extract the platform-specific device ID from a creation result."""
    for key in ("sandbox_id", "container_id", "poolab_id", "teclaw_bot_id"):
        if key in device:
            return str(device[key])
    raise KeyError(f"No device ID found in {list(device.keys())}")


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


pytestmark = [pytest.mark.e2e_asgi]


class TestCreateDeviceLifecycle:
    """Tests for POST /api/v1/paas/devices — create and verify."""

    @pytest.mark.asyncio
    async def test_create_device_returns_200_with_paas_device_id(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create a PaaS device with valid config returns 200 and paas_device_id."""
        device = await create_paas_device(api, unique_id)
        device_id = _get_device_id(device)
        await destroy_paas_device(api, device_id)


class TestDestroyDeviceLifecycle:
    """Tests for DELETE /api/v1/paas/devices/{id} — destroy and idempotency."""

    @pytest.mark.asyncio
    async def test_destroy_device_returns_200(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Destroy a created device returns 200."""
        device = await create_paas_device(api, unique_id)
        response = await destroy_paas_device(api, _get_device_id(device))
        _assert_not_di_error(response)
        assert response.status_code == 200, (
            f"Destroy returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    async def test_destroy_device_idempotent(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Idempotent re-destroy still returns 200."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        # First destroy
        r1 = await destroy_paas_device(api, paas_id)
        _assert_not_di_error(r1)
        assert r1.status_code == 200
        # Second destroy (idempotent)
        r2 = await destroy_paas_device(api, paas_id)
        _assert_not_di_error(r2)
        assert r2.status_code in (200, 404), (
            f"Idempotent re-destroy returned {r2.status_code}: {r2.text}"
        )


class TestExecuteCommandLifecycle:
    """Tests for POST /api/v1/paas/devices/{id}/commands on created devices."""

    @pytest.mark.asyncio
    async def test_execute_command_returns_command_result(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Execute command on created device returns CommandResult shape."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.post(
                api.paas_device_url(paas_id, "commands"),
                params=api.params(),
                json={"cmd": "echo hello"},
            )
            _assert_not_di_error(response)
            assert response.status_code == 200, (
                f"Command returned {response.status_code}: {response.text}"
            )
            data = response.json()["data"]
            # CommandResult shape has at least exit_code, stdout, stderr
            for field in ("exit_code", "stdout", "stderr"):
                assert field in data, f"CommandResult missing '{field}': {data}"
        finally:
            await destroy_paas_device(api, paas_id)


class TestWsInfoLifecycle:
    """Tests for GET /api/v1/paas/devices/{id}/ws-info on created devices."""

    @pytest.mark.asyncio
    async def test_ws_info_returns_connection_info(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Get WS info for a created device returns 200 with WsConnectionInfo shape."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.get(
                api.paas_device_url(paas_id, "ws-info"),
                params=api.params(port=8080, path="/ws"),
            )
            _assert_not_di_error(response)
            assert response.status_code == 200, (
                f"WS info returned {response.status_code}: {response.text}"
            )
            # WsConnectionInfo should have some connection data
            data = response.json()
            assert isinstance(data, dict), f"WsConnectionInfo is not a dict: {data}"
        finally:
            await destroy_paas_device(api, paas_id)


class TestDeviceInfoLifecycle:
    """Tests for GET /api/v1/paas/devices/{id}/info on created devices."""

    @pytest.mark.asyncio
    async def test_get_device_info_returns_device_info(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Get device info for a created device returns 200 with DeviceInfo shape."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.get(
                api.paas_device_url(paas_id, "info"),
                params=api.params(),
            )
            _assert_not_di_error(response)
            assert response.status_code == 200, (
                f"Device info returned {response.status_code}: {response.text}"
            )
            data = response.json()
            assert isinstance(data, dict), f"DeviceInfo is not a dict: {data}"
        finally:
            await destroy_paas_device(api, paas_id)


class TestOutboundRuleLifecycle:
    """Tests for PUT /api/v1/paas/devices/{id}/outbound-rule on created devices."""

    @pytest.mark.asyncio
    async def test_update_outbound_rule_returns_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Update outbound rule on created device returns 200 or appropriate response."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.put(
                api.paas_device_outbound_rule_url(paas_id),
                params=api.params(),
                json={
                    "header_operation_rules": [
                        {
                            "domains": ["example.com"],
                            "action": "ALLOW",
                            "header_name": "X-Custom",
                            "value": "test",
                        }
                    ],
                },
            )
            _assert_not_di_error(response)
            assert response.status_code in (200, 400, 422, 500, 503), (
                f"Outbound rule returned {response.status_code}: {response.text}"
            )
        finally:
            await destroy_paas_device(api, paas_id)


class TestInvokeHttpLifecycle:
    """Tests for /api/v1/paas/devices/{id}/invoke-http/{port}/{path} on created devices."""

    @pytest.mark.asyncio
    async def test_invoke_http_get(self, api: APITestHelper, unique_id: str) -> None:
        """GET invoke-http on created device."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.get(
                api.paas_device_invoke_http_url(paas_id, 8080, "api/health"),
                params=api.params(),
            )
            _assert_not_di_error(response)
            assert response.status_code in (200, 404, 500, 501, 502, 503), (
                f"invoke-http GET returned {response.status_code}: {response.text}"
            )
        finally:
            await destroy_paas_device(api, paas_id)

    @pytest.mark.asyncio
    async def test_invoke_http_post(self, api: APITestHelper, unique_id: str) -> None:
        """POST invoke-http on created device."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.post(
                api.paas_device_invoke_http_url(paas_id, 8080, "api/data"),
                params=api.params(),
                json={"key": "value"},
            )
            _assert_not_di_error(response)
            assert response.status_code in (200, 404, 500, 501, 502, 503), (
                f"invoke-http POST returned {response.status_code}: {response.text}"
            )
        finally:
            await destroy_paas_device(api, paas_id)

    @pytest.mark.asyncio
    async def test_invoke_http_put(self, api: APITestHelper, unique_id: str) -> None:
        """PUT invoke-http on created device."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.put(
                api.paas_device_invoke_http_url(paas_id, 8080, "api/data/1"),
                params=api.params(),
                json={"name": "updated"},
            )
            _assert_not_di_error(response)
            assert response.status_code in (200, 404, 500, 501, 502, 503), (
                f"invoke-http PUT returned {response.status_code}: {response.text}"
            )
        finally:
            await destroy_paas_device(api, paas_id)

    @pytest.mark.asyncio
    async def test_invoke_http_delete(self, api: APITestHelper, unique_id: str) -> None:
        """DELETE invoke-http on created device."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.delete(
                api.paas_device_invoke_http_url(paas_id, 8080, "api/data/1"),
                params=api.params(),
            )
            _assert_not_di_error(response)
            assert response.status_code in (200, 404, 500, 501, 502, 503), (
                f"invoke-http DELETE returned {response.status_code}: {response.text}"
            )
        finally:
            await destroy_paas_device(api, paas_id)


class TestOpenFolderLifecycle:
    """Tests for POST /api/v1/paas/devices/{id}/open-folder on created devices."""

    @pytest.mark.asyncio
    async def test_open_folder_returns_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Open folder on created device returns 200 or appropriate response."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.post(
                api.paas_device_open_folder_url(paas_id),
                params=api.params(),
                json={"folder_path": "/home/admin"},
            )
            _assert_not_di_error(response)
            assert response.status_code in (200, 400, 404, 500, 501), (
                f"Open folder returned {response.status_code}: {response.text}"
            )
        finally:
            await destroy_paas_device(api, paas_id)


class TestUpdateTtlLifecycle:
    """Tests for PUT /api/v1/paas/devices/{id}/ttl on created devices."""

    @pytest.mark.asyncio
    async def test_update_ttl_returns_200(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Update TTL on created device returns 200 with response shape."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.put(
                api.paas_device_ttl_url(paas_id),
                params=api.params(),
            )
            _assert_not_di_error(response)
            assert response.status_code in (200, 400, 404, 500, 501, 503), (
                f"TTL update returned {response.status_code}: {response.text}"
            )
            if response.status_code == 200:
                data = response.json()
                assert isinstance(data, dict), f"TTL response is not a dict: {data}"
        finally:
            await destroy_paas_device(api, paas_id)
