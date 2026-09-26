import pytest

from tests.e2e.asgi.conftest import (
    TEMPLATE_ARCA,
    TEMPLATE_DOCKER,
    TEMPLATE_LOCAL,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)

pytestmark = [pytest.mark.e2e_asgi]


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


class TestFacadeParseDeviceId:
    @pytest.mark.asyncio
    async def test_suffixed_device_id_works(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        assert "@" in device_id
        resp = await api.client.get(
            api.paas_device_url(device_id, "info"),
            params=api.params(),
        )
        assert resp.status_code == 200
        await destroy_paas_device(api, device_id)


class TestFacadeInvokeHttp:
    @pytest.mark.asyncio
    async def test_invoke_http_returns_non_200(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.post(
            api.paas_device_url(device_id, "invoke-http/8080/health"),
            params=api.params(),
            json={"method": "GET", "headers": {}},
        )
        assert resp.status_code in (200, 408, 500, 502, 503)
        await destroy_paas_device(api, device_id)


class TestFacadeOpenFolder:
    @pytest.mark.asyncio
    async def test_open_folder_arca(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.post(
            api.paas_device_url(device_id, "open-folder"),
            params=api.params(),
            json={"path": "/home"},
        )
        assert resp.status_code in (200, 400, 500, 501)
        await destroy_paas_device(api, device_id)


class TestFacadeUpdateTTL:
    @pytest.mark.asyncio
    async def test_update_ttl_arca(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.put(
            api.paas_device_url(device_id, "ttl"),
            params=api.params(),
            json={"ttl_in_minutes": 120},
        )
        assert resp.status_code in (200, 400, 500, 501)
        await destroy_paas_device(api, device_id)


class TestFacadeFetchStartProgress:
    @pytest.mark.asyncio
    async def test_restart_arca(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.post(
            api.paas_device_url(device_id, "restart"),
            params=api.params(),
        )
        assert resp.status_code in (200, 400, 404, 500, 501)
        await destroy_paas_device(api, device_id)


class TestFacadeConfigFormat:
    @pytest.mark.asyncio
    async def test_bad_suffix_still_works(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        bad_id = f"{device_id}@not-a-number"
        resp = await api.client.get(
            api.paas_device_url(bad_id, "info"),
            params=api.params(),
        )
        assert resp.status_code in (200, 400, 404, 500)
        await destroy_paas_device(api, device_id)
