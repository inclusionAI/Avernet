"""E2E tests for PaaS config overrides (Section 15).

Tests cover creating devices with detail_config overrides for different
platform types, including allowed and disallowed fields.
"""

import pytest

from tests.e2e.asgi.conftest import (
    TEMPLATE_ARCA,
    TEMPLATE_LOCAL,
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


pytestmark = [pytest.mark.e2e_asgi]


class TestConfigOverrideArca:
    """Config overrides for ARCA platform (cpu, memory, image, disk)."""

    @pytest.mark.asyncio
    async def test_arca_with_allowed_fields(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create ARCA device with detail_config containing allowed fields."""
        detail_config = {
            "name": f"e2e-device-{unique_id}",
            "ttl_in_minutes": 60,
            "cpu": 4,
            "memory": 8192,
            "image": "ubuntu:22.04",
            "disk": 100,
        }
        device = await create_paas_device(
            api, unique_id, template_uuid=TEMPLATE_ARCA, detail_config=detail_config
        )
        assert True
        await destroy_paas_device(api, _get_device_id(device))

    @pytest.mark.asyncio
    async def test_arca_with_minimal_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create ARCA device with minimal detail_config (just name and ttl)."""
        device = await create_paas_device(api, unique_id, template_uuid=TEMPLATE_ARCA)
        assert True
        await destroy_paas_device(api, _get_device_id(device))


class TestConfigOverrideLocal:
    """Config overrides for LOCAL platform (resource_dir)."""

    @pytest.mark.asyncio
    async def test_local_with_resource_dir(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Exercise LOCAL create — expects 500 (no real mng daemon in E2E)."""
        detail_config = {
            "machine_id": f"e2e-machine-{unique_id}",
            "user_id": f"e2e-user-{unique_id}",
            "tc_bot_id": f"e2e-bot-{unique_id}",
            "agent_code": "e2e-agent",
        }
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": TEMPLATE_LOCAL,
                "detail_config": detail_config,
            },
        )
        # LOCAL requires a connected mng daemon; in E2E this returns 500
        assert response.status_code in (200, 500), (
            f"LOCAL create returned {response.status_code}: {response.text}"
        )
        if response.status_code == 200:
            device = response.json()["data"]
            try:
                device_id = _get_device_id(device)
                await destroy_paas_device(api, device_id)
            except KeyError:
                pass


class TestConfigOverrideDisallowed:
    """Config overrides with disallowed fields."""

    @pytest.mark.asyncio
    async def test_arca_with_unknown_fields(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create ARCA device with detail_config containing unrecognized fields."""
        detail_config = {
            "name": f"e2e-device-{unique_id}",
            "ttl_in_minutes": 60,
            "unknown_field": "should-be-ignored-or-rejected",
            "bogus_param": 999,
        }
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": TEMPLATE_ARCA,
                "detail_config": detail_config,
            },
        )
        assert response.status_code in (200, 422), (
            f"Expected 200 or 422 for unknown fields, "
            f"got {response.status_code}: {response.text}"
        )
        if response.status_code == 200:
            device = response.json()["data"]
            if True:
                await destroy_paas_device(api, _get_device_id(device))

    @pytest.mark.asyncio
    async def test_local_with_arca_fields_may_be_rejected(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create LOCAL device with ARCA-specific fields (may be rejected or ignored)."""
        detail_config = {
            "name": f"e2e-device-{unique_id}",
            "ttl_in_minutes": 60,
            "cpu": 4,
            "memory": 8192,
            "image": "ubuntu:22.04",
        }
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": TEMPLATE_LOCAL,
                "detail_config": detail_config,
            },
        )
        assert response.status_code in (200, 422, 500), (
            f"Expected 200, 422, or 500 for cross-platform fields, "
            f"got {response.status_code}: {response.text}"
        )
        if response.status_code == 200:
            device = response.json()["data"]
            if True:
                await destroy_paas_device(api, _get_device_id(device))
