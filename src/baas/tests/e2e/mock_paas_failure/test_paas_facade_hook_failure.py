"""E2E tests for PaaS facade hook failure via direct HTTP.

Exercised when app runs with PAAS_MOCK_HOOK_FAILURE=1.
Hits POST /api/v1/paas/devices directly (not through bot lifecycle).

PAAS_MOCK_HOOK_FAILURE only affects execute_command() — it returns
CommandResult(exit_code=1) inside a {code, message, data} envelope at HTTP 200.
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.mock_paas_hook_failure]


class TestPaasFacadeHookFailure:
    @pytest.mark.asyncio
    async def test_execute_command_returns_exit_code_1(
        self, api: APITestHelper
    ) -> None:
        response = await api.client.post(
            api.paas_device_url("mock-sandbox-000000000001", "commands"),
            params=api.params(),
            json={"cmd": "echo hello"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["exit_code"] == 1
        assert "mock hook failure" in data["data"]["stderr"]
