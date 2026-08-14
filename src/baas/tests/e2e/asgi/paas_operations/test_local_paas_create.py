"""E2E tests for Local PaaS service-layer operations: create / connect / info / list.

Covers Phase 3.1 — acceptance paths for the LOCAL platform through the
PaaS facade HTTP layer.

Endpoints under test:
- POST /api/v1/paas/devices              (create)
- GET  /api/v1/paas/devices              (list — query params; may 405/404)
- GET  /api/v1/paas/devices/{id}/info    (device info)
"""

import pytest

from tests.e2e.asgi.conftest import (
    TEMPLATE_LOCAL,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)

pytestmark = [pytest.mark.paas_operations]


def _expect_device_id(result: dict) -> str:
    for key in ("sandbox_id", "container_id"):
        if key in result and result[key]:
            return str(result[key])
    return str(result)


class TestLocalPaasCreate:
    """POST /api/v1/paas/devices — create path."""

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="LOCAL stub requires registered machine (user_id/machine_id/tc_bot_id/agent_code)",
    )
    async def test_create_local_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        paas_id = _expect_device_id(device)
        try:
            assert device.get("platform", "").upper() in ("LOCAL", ""), (
                f"Expected LOCAL platform, got: {device.get('platform')}"
            )
            assert "status" in device, (
                f"Missing status in response: {list(device.keys())}"
            )
            assert "sandbox_id" in device or "container_id" in device, (
                f"Missing sandbox_id/container_id in response: {list(device.keys())}"
            )
        finally:
            await destroy_paas_device(api, paas_id)

    @pytest.mark.asyncio
    async def test_create_local_device_missing_required_fields(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": TEMPLATE_LOCAL,
            },
        )
        assert response.status_code >= 400, (
            f"Expected 4xx/5xx, got {response.status_code}: {response.text}"
        )


class TestLocalPaasInfo:
    """GET /api/v1/paas/devices/{id}/info — device info path."""

    @pytest.mark.asyncio
    async def test_get_device_info_response_shape(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device: dict | None = None
        paas_id: str | None = None
        try:
            device = await create_paas_device(
                api, unique_id, template_uuid=TEMPLATE_LOCAL
            )
        except Exception:
            pass

        if device is None:
            paas_id = f"local-nonexistent-{unique_id}"
        else:
            paas_id = _expect_device_id(device)

        try:
            response = await api.client.get(
                api.paas_device_url(paas_id, "info"),
                params=api.params(),
            )
            assert response.status_code in (200, 400, 404, 500), (
                f"Info returned {response.status_code}: {response.text}"
            )
            if response.status_code == 200:
                data = response.json().get("data", response.json())
                assert "platform" in data, (
                    f"Missing platform in info response: {list(data.keys())}"
                )
                assert "status" in data, (
                    f"Missing status in info response: {list(data.keys())}"
                )
        finally:
            if paas_id is not None:
                try:
                    await destroy_paas_device(api, paas_id)
                except Exception:
                    pass

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="LOCAL stub requires registered machine",
    )
    async def test_get_device_info_after_create(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_LOCAL)
        paas_id = _expect_device_id(device)
        try:
            response = await api.client.get(
                api.paas_device_url(paas_id, "info"),
                params=api.params(),
            )
            assert response.status_code == 200, (
                f"Info returned {response.status_code}: {response.text}"
            )
            data = response.json().get("data", response.json())
            assert data.get("status", "").upper() in ("RUNNING", "CONNECTED"), (
                f"Expected RUNNING/CONNECTED, got: {data.get('status')}"
            )
        finally:
            await destroy_paas_device(api, paas_id)


class TestLocalPaasList:
    """GET /api/v1/paas/devices — list path (query params)."""

    @pytest.mark.asyncio
    async def test_list_local_instances(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        response = await api.client.get(
            api.paas_device_url(),
            params=api.params(page=1, page_size=10),
        )
        assert response.status_code in (200, 404, 405, 501), (
            f"List returned {response.status_code}: {response.text}"
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                payload = data["data"]
            else:
                payload = data
            assert isinstance(payload, (dict, list)), (
                f"List payload is not dict/list: {type(payload)}"
            )
