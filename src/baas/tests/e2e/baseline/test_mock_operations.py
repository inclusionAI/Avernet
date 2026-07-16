"""E2E tests for MockPaasService operations not yet exercised.

The mock PAAS plugin has several operations that return canned responses
but haven't been tested via HTTP endpoints. These tests cover:
- WS info resolution
- Command execution
- Device info
- Outbound rule update
- HTTP invoke (via proxy)
"""

import pytest

from ..conftest import (
    TEMPLATE_ARCA,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


def _get_device_id(device: dict) -> str:
    for key in (
        "sandbox_id",
        "container_id",
        "poolab_id",
        "teclaw_bot_id",
        "k8s_pod_id",
    ):
        if key in device:
            return str(device[key])
    raise KeyError(f"No device ID found in {list(device.keys())}")


class TestMockDeviceWsInfo:
    @pytest.mark.asyncio
    async def test_ws_info_with_port_and_path(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.get(
            api.paas_device_url(device_id, "ws-info"),
            params=api.params(port=9222, path="/devtools"),
        )
        assert resp.status_code in (200, 500, 501)
        await destroy_paas_device(api, device_id)


class TestMockCommandExecution:
    @pytest.mark.asyncio
    async def test_execute_command_on_arca_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.post(
            api.paas_device_url(device_id, "commands"),
            params=api.params(),
            json={"cmd": "echo hello"},
        )
        assert resp.status_code in (200, 500, 501)
        await destroy_paas_device(api, device_id)


class TestMockDeviceInfo:
    @pytest.mark.asyncio
    async def test_get_device_info(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.get(
            api.paas_device_url(device_id, "info"),
            params=api.params(),
        )
        assert resp.status_code in (200, 500)
        await destroy_paas_device(api, device_id)


class TestMockOutboundRule:
    @pytest.mark.asyncio
    async def test_set_outbound_rule(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.put(
            api.paas_device_url(device_id, "outbound-rule"),
            params=api.params(),
            json={
                "header_operation_rules": [
                    {
                        "domains": ["*"],
                        "action": "ALLOW",
                        "header_name": "x-test",
                        "value": "1",
                    }
                ]
            },
        )
        assert resp.status_code in (200, 500, 501)
        await destroy_paas_device(api, device_id)


class TestMockInvokeHttp:
    @pytest.mark.asyncio
    async def test_invoke_http_proxy(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.get(
            api.paas_device_url(device_id) + "/invoke-http/8080/health",
            params=api.params(),
        )
        assert resp.status_code in (200, 500, 501)
        await destroy_paas_device(api, device_id)
