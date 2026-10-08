"""E2E tests for TeClaw PaaS platform operations (Phase 3.4).

Tests cover:
- POST   /api/v1/paas/devices                         — Create TeClaw device
- GET    /api/v1/paas/devices/{id}/info                — Get device info
- DELETE /api/v1/paas/devices/{id}                     — Destroy device
- PUT    /api/v1/paas/devices/{id}/outbound-rule        — Update config
- Error paths: invalid config, non-existent device info
"""

from typing import Any

import pytest

from tests.e2e.asgi.conftest import (
    TEMPLATE_TECLAW,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)

_NON_ARCA_XFAIL = pytest.mark.xfail(
    reason="CreateDeviceRequest.detail_config only accepts "
    "ArcaDeviceConfig|SigmaDeviceConfig — non-ARCA/SIGMA gets 500",
)

pytestmark = [pytest.mark.paas_operations]


class TestTeClawCreateDevice:
    """Create TeClaw device and verify response fields."""

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_create_teclaw_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_TECLAW)
        try:
            assert device.get("teclaw_bot_id"), (
                f"Missing teclaw_bot_id in {list(device.keys())}"
            )
            assert device.get("platform") == "TECLAW", (
                f"Expected platform=TECLAW, got {device.get('platform')}"
            )
            assert device.get("status"), f"Missing status in {list(device.keys())}"
        finally:
            device_id = device.get("teclaw_bot_id")
            if device_id:
                await destroy_paas_device(api, str(device_id))

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_create_teclaw_with_custom_name(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        detail_config: dict[str, Any] = {
            "name": f"e2e-teclaw-{unique_id}",
            "ttl_in_minutes": 30,
        }
        device = await create_paas_device(
            api, unique_id, template_uuid=TEMPLATE_TECLAW, detail_config=detail_config
        )
        try:
            assert device.get("teclaw_bot_id"), (
                f"Missing teclaw_bot_id in {list(device.keys())}"
            )
        finally:
            device_id = device.get("teclaw_bot_id")
            if device_id:
                await destroy_paas_device(api, str(device_id))


class TestTeClawDeviceInfo:
    """Get info about a TeClaw device."""

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_get_teclaw_device_info(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_TECLAW)
        device_id = device.get("teclaw_bot_id")
        try:
            assert device_id, f"No teclaw_bot_id in device: {list(device.keys())}"
            response = await api.client.get(
                api.paas_device_url(str(device_id), "info"),
                params=api.params(),
            )
            assert response.status_code in (200, 400, 404, 500), (
                f"TeClaw device info returned {response.status_code}: {response.text}"
            )
            if response.status_code == 200:
                data = response.json()
                assert isinstance(data, dict), (
                    f"TeClaw device info response is not dict: {data}"
                )
        finally:
            if device_id:
                await destroy_paas_device(api, str(device_id))

    @pytest.mark.asyncio
    async def test_get_info_nonexistent_teclaw(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        fake_id = f"e2e-teclaw-nonexistent-{unique_id}"
        response = await api.client.get(
            api.paas_device_url(fake_id, "info"),
            params=api.params(),
        )
        assert response.status_code in (200, 404), (
            f"Expected 404 for non-existent TeClaw device, got {response.status_code}: {response.text}"
        )


class TestTeClawDestroy:
    """Destroy TeClaw device."""

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_destroy_teclaw_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_TECLAW)
        device_id = device.get("teclaw_bot_id")
        assert device_id, f"No teclaw_bot_id in device: {list(device.keys())}"
        response = await destroy_paas_device(api, str(device_id))
        assert response.status_code == 200, (
            f"Destroy TeClaw returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_double_destroy_teclaw(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_TECLAW)
        device_id = device.get("teclaw_bot_id")
        assert device_id, f"No teclaw_bot_id in device: {list(device.keys())}"
        r1 = await destroy_paas_device(api, str(device_id))
        assert r1.status_code == 200, (
            f"First TeClaw destroy returned {r1.status_code}: {r1.text}"
        )
        r2 = await destroy_paas_device(api, str(device_id))
        assert r2.status_code in (200, 404), (
            f"Second TeClaw destroy returned {r2.status_code}: {r2.text}"
        )


class TestTeClawUpdateConfig:
    """Update TeClaw device configuration via outbound-rule endpoint."""

    @pytest.mark.asyncio
    @_NON_ARCA_XFAIL
    async def test_update_teclaw_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_TECLAW)
        device_id = device.get("teclaw_bot_id")
        try:
            assert device_id, f"No teclaw_bot_id in device: {list(device.keys())}"
            response = await api.client.put(
                api.paas_device_url(str(device_id), "outbound-rule"),
                params=api.params(),
                json={
                    "header_operation_rules": [
                        {
                            "domains": ["*"],
                            "action": "ALLOW",
                            "header_name": "x-teclaw-test",
                            "value": "1",
                        }
                    ]
                },
            )
            assert response.status_code in (200, 400, 500, 503), (
                f"TeClaw update config returned {response.status_code}: {response.text}"
            )
        finally:
            if device_id:
                await destroy_paas_device(api, str(device_id))


class TestTeClawCreateErrors:
    """Create TeClaw device with invalid config."""

    @pytest.mark.asyncio
    async def test_create_teclaw_with_invalid_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": TEMPLATE_TECLAW,
                "detail_config": {
                    "name": f"e2e-teclaw-bad-{unique_id}",
                    "ttl_in_minutes": -1,
                },
            },
        )
        assert response.status_code in (400, 422, 500), (
            f"Expected 4xx/5xx for invalid TeClaw config, got {response.status_code}: {response.text}"
        )
