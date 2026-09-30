import pytest

from tests.e2e.asgi.conftest import (
    TEMPLATE_ARCA,
    TEMPLATE_DOCKER,
    TEMPLATE_K8S,
    TEMPLATE_LOCAL,
    TEMPLATE_POOLAB,
    TEMPLATE_SIGMA,
    TEMPLATE_TECLAW,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)

pytestmark = [pytest.mark.paas_operations]


def _get_device_id(device: dict) -> str:
    for key in (
        "sandbox_id",
        "container_id",
        "teclaw_bot_id",
        "instance_id",
        "poolab_id",
    ):
        if key in device:
            return str(device[key])
    raise KeyError(f"No device ID found in {list(device.keys())}")


def _assert_response_shape(device: dict) -> None:
    assert isinstance(device, dict), f"Device response is not a dict: {device}"
    assert _get_device_id(device)


class TestFacadeDispatchArca:
    @pytest.mark.asyncio
    async def test_dispatch_to_arca_creates_device_with_platform(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        try:
            assert device.get("platform", "").upper() == "ARCA"
            _assert_response_shape(device)
        finally:
            await destroy_paas_device(api, _get_device_id(device))


class TestFacadeDispatchSigma:
    @pytest.mark.xfail(
        reason="SIGMA template requires valid sigma config fields not present in default detail_config",
    )
    @pytest.mark.asyncio
    async def test_dispatch_to_sigma_creates_device_with_platform(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_SIGMA)
        try:
            assert device.get("platform", "").upper() == "SIGMA"
            _assert_response_shape(device)
        finally:
            await destroy_paas_device(api, _get_device_id(device))


class TestFacadeDispatchLocal:
    @pytest.mark.xfail(reason="LOCAL stub requires registered machine", strict=False)
    @pytest.mark.asyncio
    async def test_dispatch_to_local_creates_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        try:
            _assert_response_shape(device)
        finally:
            await destroy_paas_device(api, _get_device_id(device))


class TestFacadeDispatchPoolab:
    @pytest.mark.xfail(
        reason="CreateDeviceRequest.detail_config only accepts ArcaDeviceConfig|SigmaDeviceConfig",
        strict=False,
    )
    @pytest.mark.asyncio
    async def test_dispatch_to_poolab_returns_error(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_POOLAB)
        try:
            _assert_response_shape(device)
        finally:
            try:
                await destroy_paas_device(api, _get_device_id(device))
            except Exception:
                pass


class TestFacadeDispatchTeclaw:
    @pytest.mark.xfail(
        reason="CreateDeviceRequest.detail_config only accepts ArcaDeviceConfig|SigmaDeviceConfig",
        strict=False,
    )
    @pytest.mark.asyncio
    async def test_dispatch_to_teclaw_returns_error(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_TECLAW)
        try:
            _assert_response_shape(device)
        finally:
            try:
                await destroy_paas_device(api, _get_device_id(device))
            except Exception:
                pass


class TestFacadeDispatchK8s:
    @pytest.mark.xfail(
        reason="CreateDeviceRequest.detail_config only accepts ArcaDeviceConfig|SigmaDeviceConfig",
        strict=False,
    )
    @pytest.mark.asyncio
    async def test_dispatch_to_k8s_returns_error(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_K8S)
        try:
            _assert_response_shape(device)
        finally:
            try:
                await destroy_paas_device(api, _get_device_id(device))
            except Exception:
                pass


class TestFacadeDispatchDocker:
    @pytest.mark.xfail(
        reason="CreateDeviceRequest.detail_config only accepts ArcaDeviceConfig|SigmaDeviceConfig",
        strict=False,
    )
    @pytest.mark.asyncio
    async def test_dispatch_to_docker_returns_error(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_DOCKER)
        try:
            _assert_response_shape(device)
        finally:
            try:
                await destroy_paas_device(api, _get_device_id(device))
            except Exception:
                pass


class TestFacadeInvalidTemplate:
    @pytest.mark.asyncio
    async def test_invalid_template_uuid_returns_error(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": "INVALID-UUID-not-exists",
                "detail_config": {
                    "name": f"e2e-device-{unique_id}",
                    "ttl_in_minutes": 60,
                },
            },
        )
        assert response.status_code >= 400

    @pytest.mark.asyncio
    async def test_missing_template_uuid_returns_error(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "detail_config": {
                    "name": f"e2e-device-{unique_id}",
                    "ttl_in_minutes": 60,
                },
            },
        )
        assert response.status_code >= 400


class TestFactoryPlatformSelection:
    @pytest.mark.asyncio
    async def test_arca_template_returns_platform_arca(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        try:
            platform = device.get("platform", "")
            assert platform.upper() == "ARCA", f"Expected ARCA, got {platform}"
        finally:
            await destroy_paas_device(api, _get_device_id(device))

    @pytest.mark.xfail(
        reason="SIGMA template requires valid sigma config fields not present in default detail_config",
    )
    @pytest.mark.asyncio
    async def test_sigma_template_returns_platform_sigma(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_SIGMA)
        try:
            platform = device.get("platform", "")
            assert platform.upper() == "SIGMA", f"Expected SIGMA, got {platform}"
        finally:
            await destroy_paas_device(api, _get_device_id(device))
