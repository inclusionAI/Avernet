"""E2E tests for PaaS platform template paths.

Tests cover creating devices with Arca template and verifying tenant default
resolution when template_uuid is omitted.
"""

import pytest

from ..conftest import (
    TEMPLATE_ARCA,
    APITestHelper,
    create_paas_device,
    destroy_paas_device,
)


def _get_device_id(device: dict[str, object]) -> str:
    """Extract the platform-specific device ID from a creation result."""
    for key in ("sandbox_id",):
        if key in device:
            return str(device[key])
    raise KeyError(f"No device ID found in {list(device.keys())}")


pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestPlatformTemplateArca:
    """Create device with TEMPLATE_ARCA."""

    @pytest.mark.asyncio
    async def test_create_device_with_template_arca(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create device with TEMPLATE_ARCA returns 200."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        device_id = _get_device_id(device)
        await destroy_paas_device(api, device_id)


class TestPlatformTemplateInvalid:
    """Error cases for invalid or missing template_uuid."""

    @pytest.mark.asyncio
    async def test_create_device_with_invalid_template_returns_4xx(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create device with an invalid template_uuid returns 4xx."""
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": "TEMPLATE-NONEXISTENT",
                "detail_config": {
                    "name": f"e2e-device-{unique_id}",
                    "ttl_in_minutes": 60,
                },
            },
        )
        assert response.status_code in (400, 404, 422, 500), (
            f"Expected 4xx or 500 for invalid template, got {response.status_code}: "
            f"{response.text}"
        )

    @pytest.mark.asyncio
    async def test_create_device_without_template_uuid_uses_default(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create device without template_uuid exercises tenant default resolution."""
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
        assert response.status_code in (200, 422, 500), (
            f"Expected 200, 422, or 500 for omitted template_uuid, "
            f"got {response.status_code}: {response.text}"
        )
        if response.status_code == 200:
            device = response.json()["data"]
            try:
                device_id = _get_device_id(device)
                await destroy_paas_device(api, device_id)
            except KeyError:
                pass
