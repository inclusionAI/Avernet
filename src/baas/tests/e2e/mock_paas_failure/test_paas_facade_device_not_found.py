"""E2E tests for PaaS facade device-not-found via direct HTTP.

Exercised when app runs with PAAS_MOCK_DEVICE_NOT_FOUND=1.

PAAS_MOCK_DEVICE_NOT_FOUND only triggers PaasError(DEVICE_NOT_FOUND)
in destroy_device(). The facade's DELETE endpoint returns HTTP 200
with the error serialized in the response. Other facade endpoints
(get_device_info, execute_command) go through DB lookup first, so
uncreated devices receive 500 (DB error) or 200 (mock fallthrough).
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.mock_paas_device_not_found]


class TestPaasFacadeDeviceNotFound:
    @pytest.mark.asyncio
    async def test_destroy_nonexistent_device(self, api: APITestHelper) -> None:
        response = await api.client.delete(
            api.paas_device_url("no-such-device@0"),
            params=api.params(),
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_device_info_nonexistent_device(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.paas_device_url("no-such-device@0", "info"),
            params=api.params(),
        )
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_execute_command_nonexistent_device(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.paas_device_url("no-such-device@0", "commands"),
            params=api.params(),
            json={"cmd": "echo hello"},
        )
        assert response.status_code == 200
