"""E2E tests for PaaS facade destroy device failure via direct HTTP.

Exercised when app runs with PAAS_MOCK_DESTROY_FAILURE=1.
Hits DELETE /api/v1/paas/devices/{id} directly (not through bot lifecycle).
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.mock_paas_destroy_failure]


class TestPaasFacadeDestroyFailure:
    @pytest.mark.asyncio
    async def test_destroy_device_returns_error_with_mock_destroy_failure(
        self, api: APITestHelper
    ) -> None:
        response = await api.client.delete(
            api.paas_device_url("test-device@1"),
            params=api.params(),
        )
        assert response.status_code in (400, 404, 500, 503)

    @pytest.mark.asyncio
    async def test_destroy_device_with_suffix_fails(self, api: APITestHelper) -> None:
        response = await api.client.delete(
            api.paas_device_url("test-device@0"),
            params=api.params(),
        )
        assert response.status_code in (400, 404, 500, 503)

    @pytest.mark.asyncio
    async def test_idempotent_destroy_still_reports_failure(
        self, api: APITestHelper
    ) -> None:
        response = await api.client.delete(
            api.paas_device_url("already-destroyed@1"),
            params=api.params(),
        )
        assert response.status_code in (400, 404, 500, 503)
