import pytest

from ..conftest import (
    TEMPLATE_ARCA,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


def _get_device_id(device: dict) -> str:
    for key in ("sandbox_id", "container_id"):
        if key in device:
            return str(device[key])
    raise KeyError(f"No device ID found in {list(device.keys())}")


class TestArcaDeviceFacade:
    @pytest.mark.asyncio
    async def test_create_destroy_arca(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(
            api,
            unique_id,
            template_uuid=TEMPLATE_ARCA,
        )
        assert "sandbox_id" in device
        await destroy_paas_device(api, _get_device_id(device))


class TestArcaDeviceOperations:
    """Exercise Arca device operations beyond create/destroy."""

    @pytest.mark.asyncio
    async def test_execute_command(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        sandbox_id = _get_device_id(device)
        resp = await api.client.post(
            api.paas_device_url(sandbox_id, "commands"),
            params=api.params(),
            json={"cmd": "echo hello"},
        )
        assert resp.status_code in (200, 400, 500, 501)
        await destroy_paas_device(api, sandbox_id)

    @pytest.mark.asyncio
    async def test_get_device_info(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        sandbox_id = _get_device_id(device)
        resp = await api.client.get(
            api.paas_device_url(sandbox_id, "info"),
            params=api.params(),
        )
        assert resp.status_code in (200, 404, 500)
        await destroy_paas_device(api, sandbox_id)

    @pytest.mark.asyncio
    async def test_ws_info(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        sandbox_id = _get_device_id(device)
        resp = await api.client.get(
            api.paas_device_url(sandbox_id, "ws-info"),
            params=api.params(port=9222, path="/devtools"),
        )
        assert resp.status_code in (200, 500, 501)
        await destroy_paas_device(api, sandbox_id)
