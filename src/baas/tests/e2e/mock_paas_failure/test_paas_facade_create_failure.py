"""E2E tests for PaaS facade create device failure via direct HTTP.

Exercised when app runs with PAAS_MOCK_CREATE_FAILURE=1.
Hits POST /api/v1/paas/devices directly (not through bot lifecycle).
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.mock_paas_create_failure]


class TestPaasFacadeCreateFailure:
    @pytest.mark.asyncio
    async def test_create_device_returns_500_with_mock_create_failure(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "device_template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                "detail_config": {
                    "name": f"fail-device-{unique_id}",
                    "ttl_in_minutes": 60,
                },
            },
        )
        assert response.status_code == 500
        data = response.json()
        assert "detail" in data or "message" in data

    @pytest.mark.asyncio
    async def test_create_device_without_template_uuid_fails(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "tenant_name": api.tenant,
                "detail_config": {
                    "name": f"fail-device-{unique_id}",
                    "ttl_in_minutes": 60,
                },
            },
        )
        assert response.status_code == 500
