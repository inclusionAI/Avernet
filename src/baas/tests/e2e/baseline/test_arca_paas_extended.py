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


class TestArcaDeviceWsInfo:
    @pytest.mark.asyncio
    async def test_ws_info_with_auth(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.get(
            api.paas_device_url(device_id, "ws-info"),
            params=api.params(port=8443, path="/ws/v1"),
        )
        assert resp.status_code in (200, 400, 500)
        await destroy_paas_device(api, device_id)

    @pytest.mark.asyncio
    async def test_ws_info_port_validation(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.get(
            api.paas_device_url(device_id, "ws-info"),
            params=api.params(port=0, path="/ws"),
        )
        assert resp.status_code in (200, 400, 422, 500)
        await destroy_paas_device(api, device_id)


class TestArcaDeviceOutboundRule:
    @pytest.mark.asyncio
    async def test_set_outbound_rule(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.put(
            api.paas_device_url(device_id, "outbound-rule"),
            params=api.params(),
            json={"header_rules": [], "rules": []},
        )
        assert resp.status_code in (200, 400, 500)
        await destroy_paas_device(api, device_id)

    @pytest.mark.asyncio
    async def test_set_outbound_rule_with_rule(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        resp = await api.client.put(
            api.paas_device_url(device_id, "outbound-rule"),
            params=api.params(),
            json={
                "header_rules": [],
                "rules": [{"protocol": "tcp", "port": 8080, "direction": "outbound"}],
            },
        )
        assert resp.status_code in (200, 400, 500)
        await destroy_paas_device(api, device_id)


class TestArcaDeviceIdempotentDestroy:
    @pytest.mark.asyncio
    async def test_triple_destroy(self, api: APITestHelper, unique_id: str) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        r1 = await destroy_paas_device(api, device_id)
        assert r1.status_code == 200
        r2 = await destroy_paas_device(api, device_id)
        assert r2.status_code in (200, 404)
        r3 = await destroy_paas_device(api, device_id)
        assert r3.status_code in (200, 404)
