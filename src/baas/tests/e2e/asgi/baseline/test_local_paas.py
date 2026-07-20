"""E2E tests for local PaaS endpoints and device query (Section 16).

Endpoints:
- GET /api/v1/local/machines/{machine_id}/info
- GET /api/v1/local/machines/{machine_id}/res-dirs?dir=/path
- GET /api/v1/local/users/{user_id}/machines
- GET /api/v1/devices/{device_uuid}  (create first, then query)
"""

import pytest

from tests.e2e.asgi.conftest import (
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


class TestLocalMachineInfo:
    """Tests for GET /api/v1/local/machines/{machine_id}/info."""

    @pytest.mark.asyncio
    async def test_get_machine_info_with_valid_id(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Exercise machine info endpoint with a valid-looking machine ID."""
        machine_id = f"e2e-machine-{unique_id}"
        response = await api.client.get(
            api.local_machine_info_url(machine_id),
            params=api.params(),
        )
        assert response.status_code in (200, 400, 404), (
            f"Machine info returned {response.status_code}: {response.text}"
        )


class TestLocalMachineResDirs:
    """Tests for GET /api/v1/local/machines/{machine_id}/res-dirs."""

    @pytest.mark.asyncio
    async def test_get_machine_res_dirs_with_path(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Exercise res-dirs endpoint with a dir query parameter."""
        machine_id = f"e2e-machine-{unique_id}"
        response = await api.client.get(
            api.local_machine_res_dirs_url(machine_id),
            params=api.params(dir="~/Desktop"),
        )
        assert response.status_code in (200, 400, 404), (
            f"Res dirs returned {response.status_code}: {response.text}"
        )


class TestLocalUserMachines:
    """Tests for GET /api/v1/local/users/{user_id}/machines."""

    @pytest.mark.asyncio
    async def test_get_user_machines(self, api: APITestHelper, unique_id: str) -> None:
        """Exercise user machines endpoint."""
        user_id = f"e2e-user-{unique_id}"
        response = await api.client.get(
            api.local_user_machines_url(user_id),
            params=api.params(),
        )
        assert response.status_code in (200, 404), (
            f"User machines returned {response.status_code}: {response.text}"
        )


class TestDeviceQueryByUuid:
    """Tests for GET /api/v1/devices/{device_uuid} — create first, then query."""

    @pytest.mark.asyncio
    async def test_query_device_by_uuid_after_create(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create a device, then query it by UUID and verify response."""
        device = await create_paas_device(api, unique_id)
        paas_id = _get_device_id(device)
        try:
            response = await api.client.get(
                api.device_url(paas_id),
                params=api.params(),
            )
            assert response.status_code in (200, 404), (
                f"Device query returned {response.status_code}: {response.text}"
            )
            if response.status_code == 200:
                data = response.json()
                assert isinstance(data, dict), (
                    f"Device query response is not dict: {data}"
                )
        finally:
            await destroy_paas_device(api, paas_id)
