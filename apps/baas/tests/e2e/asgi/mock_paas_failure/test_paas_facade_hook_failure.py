from __future__ import annotations

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.mock_paas_hook_failure]


def _set_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv("PAAS_MOCK_HOOK_FAILURE", "true")


class TestPaasFacadeHookFailure:
    @pytest.mark.asyncio
    async def test_execute_command_returns_exit_code_1(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_mock(monkeypatch)
        response = await api.client.post(
            api.paas_device_url("mock-sandbox-000000000001", "commands"),
            params=api.params(),
            json={"cmd": "echo hello"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["exit_code"] == 1
        assert "mock hook failure" in data["data"]["stderr"]
