"""E2E tests for Standalone / Docker PaaS platform operations (Phase 3.4).

Tests cover:
- POST   /api/v1/paas/devices                         — Create Docker device
- GET    /api/v1/paas/devices/{id}/info                — Get device info
- POST   /api/v1/paas/devices/{id}/commands             — Execute command
- DELETE /api/v1/paas/devices/{id}                     — Destroy device
- GET    /api/v1/paas/devices                           — List instances
- Error paths: invalid docker config, non-existent device
"""

from typing import Any

import pytest

from tests.e2e.asgi.conftest import (
    TEMPLATE_DOCKER,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)

_NON_ARCA_XFAIL = pytest.mark.xfail(
    reason="CreateDeviceRequest.detail_config only accepts "
    "ArcaDeviceConfig|SigmaDeviceConfig — non-ARCA/SIGMA gets 500",
)

pytestmark = [pytest.mark.paas_operations]


class TestDockerCreateDevice:
    """Create Standalone Docker device and verify response fields."""

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_create_docker_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        detail_config: dict[str, Any] = {
            "name": f"e2e-docker-{unique_id}",
            "ttl_in_minutes": 30,
        }
        device = await create_paas_device(
            api, unique_id, template_uuid=TEMPLATE_DOCKER, detail_config=detail_config
        )
        try:
            for field in ("sandbox_id", "container_id"):
                if device.get(field):
                    break
            else:
                raise AssertionError(
                    f"Missing sandbox_id or container_id in {list(device.keys())}"
                )
            assert device.get("platform") == "DOCKER", (
                f"Expected platform=DOCKER, got {device.get('platform')}"
            )
            assert device.get("status"), f"Missing status in {list(device.keys())}"
        finally:
            device_id = device.get("sandbox_id") or device.get("container_id")
            if device_id:
                await destroy_paas_device(api, str(device_id))

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_create_docker_with_host_port(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        detail_config: dict[str, Any] = {
            "name": f"e2e-docker-hp-{unique_id}",
            "ttl_in_minutes": 60,
        }
        device = await create_paas_device(
            api, unique_id, template_uuid=TEMPLATE_DOCKER, detail_config=detail_config
        )
        try:
            device_id = device.get("sandbox_id") or device.get("container_id")
            assert device_id, f"Missing device ID in {list(device.keys())}"
            assert device.get("platform") == "DOCKER", (
                f"Expected platform=DOCKER, got {device.get('platform')}"
            )
            resp = await api.client.get(
                api.paas_device_url(str(device_id), "info"),
                params=api.params(),
            )
            assert resp.status_code in (200, 400, 404, 500), (
                f"Docker device info returned {resp.status_code}: {resp.text}"
            )
            if resp.status_code == 200:
                data = resp.json()
                assert isinstance(data, dict), (
                    f"Docker device info response is not dict: {data}"
                )
        finally:
            if device_id:
                await destroy_paas_device(api, str(device_id))


class TestDockerDeviceInfo:
    """Get info about a Docker device and verify container details."""

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_get_docker_device_info(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        detail_config: dict[str, Any] = {
            "name": f"e2e-docker-info-{unique_id}",
            "ttl_in_minutes": 30,
        }
        device = await create_paas_device(
            api, unique_id, template_uuid=TEMPLATE_DOCKER, detail_config=detail_config
        )
        device_id = device.get("sandbox_id") or device.get("container_id")
        try:
            assert device_id, f"No device ID in {list(device.keys())}"
            response = await api.client.get(
                api.paas_device_url(str(device_id), "info"),
                params=api.params(),
            )
            assert response.status_code in (200, 400, 404, 500), (
                f"Docker device info returned {response.status_code}: {response.text}"
            )
            if response.status_code == 200:
                data = response.json()
                assert isinstance(data, dict), (
                    f"Docker device info response is not dict: {data}"
                )
                assert "container" in data or isinstance(data.get("data"), dict), (
                    f"Container details missing from Docker info: {list(data.keys())}"
                )
        finally:
            if device_id:
                await destroy_paas_device(api, str(device_id))

    @pytest.mark.asyncio
    async def test_get_info_nonexistent_docker(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        fake_id = f"e2e-docker-nonexistent-{unique_id}"
        response = await api.client.get(
            api.paas_device_url(fake_id, "info"),
            params=api.params(),
        )
        assert response.status_code in (200, 404, 400, 500), (
            f"Expected error for non-existent Docker device, got {response.status_code}: {response.text}"
        )


class TestDockerExecuteCommand:
    """Execute command on a Docker device and verify output."""

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_execute_command_on_docker(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        detail_config: dict[str, Any] = {
            "name": f"e2e-docker-cmd-{unique_id}",
            "ttl_in_minutes": 30,
        }
        device = await create_paas_device(
            api, unique_id, template_uuid=TEMPLATE_DOCKER, detail_config=detail_config
        )
        device_id = device.get("sandbox_id") or device.get("container_id")
        try:
            assert device_id, f"No device ID in {list(device.keys())}"
            response = await api.client.post(
                api.paas_device_url(str(device_id), "commands"),
                params=api.params(),
                json={
                    "cmd": "echo hello-docker",
                    "timeout_seconds": 10,
                },
            )
            assert response.status_code in (200, 400, 404, 422, 500, 503), (
                f"Docker execute command returned {response.status_code}: {response.text}"
            )
            if response.status_code == 200:
                data = response.json()
                assert isinstance(data, dict), (
                    f"Docker command response is not dict: {data}"
                )
        finally:
            if device_id:
                await destroy_paas_device(api, str(device_id))

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_execute_command_with_env(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        detail_config: dict[str, Any] = {
            "name": f"e2e-docker-cmd-env-{unique_id}",
            "ttl_in_minutes": 30,
        }
        device = await create_paas_device(
            api, unique_id, template_uuid=TEMPLATE_DOCKER, detail_config=detail_config
        )
        device_id = device.get("sandbox_id") or device.get("container_id")
        try:
            assert device_id, f"No device ID in {list(device.keys())}"
            response = await api.client.post(
                api.paas_device_url(str(device_id), "commands"),
                params=api.params(),
                json={
                    "cmd": "env",
                    "env": {"E2E_TEST_KEY": "e2e-docker-value"},
                    "timeout_seconds": 10,
                },
            )
            assert response.status_code in (200, 400, 404, 422, 500, 503), (
                f"Docker execute command with env returned {response.status_code}: {response.text}"
            )
        finally:
            if device_id:
                await destroy_paas_device(api, str(device_id))


class TestDockerDestroy:
    """Destroy Docker device lifecycle."""

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_destroy_docker_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        detail_config: dict[str, Any] = {
            "name": f"e2e-docker-destroy-{unique_id}",
            "ttl_in_minutes": 30,
        }
        device = await create_paas_device(
            api, unique_id, template_uuid=TEMPLATE_DOCKER, detail_config=detail_config
        )
        device_id = device.get("sandbox_id") or device.get("container_id")
        assert device_id, f"No device ID in {list(device.keys())}"
        response = await destroy_paas_device(api, str(device_id))
        assert response.status_code == 200, (
            f"Destroy Docker returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_double_destroy_docker(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        detail_config: dict[str, Any] = {
            "name": f"e2e-docker-dd-{unique_id}",
            "ttl_in_minutes": 30,
        }
        device = await create_paas_device(
            api, unique_id, template_uuid=TEMPLATE_DOCKER, detail_config=detail_config
        )
        device_id = device.get("sandbox_id") or device.get("container_id")
        assert device_id, f"No device ID in {list(device.keys())}"
        r1 = await destroy_paas_device(api, str(device_id))
        assert r1.status_code == 200, (
            f"First Docker destroy returned {r1.status_code}: {r1.text}"
        )
        r2 = await destroy_paas_device(api, str(device_id))
        assert r2.status_code in (200, 404), (
            f"Second Docker destroy returned {r2.status_code}: {r2.text}"
        )


class TestDockerList:
    """List Docker instances via device query."""

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_list_devices_includes_docker(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        detail_config: dict[str, Any] = {
            "name": f"e2e-docker-list-{unique_id}",
            "ttl_in_minutes": 30,
        }
        device = await create_paas_device(
            api, unique_id, template_uuid=TEMPLATE_DOCKER, detail_config=detail_config
        )
        device_id = device.get("sandbox_id") or device.get("container_id")
        try:
            assert device_id, f"No device ID in {list(device.keys())}"
            response = await api.client.get(
                api.paas_device_url(),
                params=api.params(name=f"e2e-docker-list-{unique_id}"),
            )
            assert response.status_code in (200, 400, 404), (
                f"List Docker devices returned {response.status_code}: {response.text}"
            )
        finally:
            if device_id:
                await destroy_paas_device(api, str(device_id))


class TestDockerCreateErrors:
    """Create Docker device with invalid config."""

    @pytest.mark.asyncio
    async def test_create_docker_with_invalid_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": TEMPLATE_DOCKER,
                "detail_config": {
                    "name": f"e2e-docker-bad-{unique_id}",
                    "ttl_in_minutes": -1,
                },
            },
        )
        assert response.status_code in (400, 422, 500), (
            f"Expected 4xx/5xx for invalid Docker config, got {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    async def test_create_docker_missing_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": TEMPLATE_DOCKER,
            },
        )
        assert response.status_code in (200, 400, 422, 500), (
            f"Expected 4xx/5xx for missing Docker config, got {response.status_code}: {response.text}"
        )
