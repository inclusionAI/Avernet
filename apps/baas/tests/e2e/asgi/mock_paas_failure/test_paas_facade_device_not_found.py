from __future__ import annotations

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.mock_paas_device_not_found]


def _set_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv("PAAS_MOCK_DEVICE_NOT_FOUND", "true")


class TestPaasFacadeDeviceNotFound:
    @pytest.mark.asyncio
    async def test_destroy_nonexistent_device(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_mock(monkeypatch)
        response = await api.client.delete(
            api.paas_device_url("no-such-device@0"), params=api.params()
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_device_info_nonexistent_device(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_mock(monkeypatch)
        response = await api.client.get(
            api.paas_device_url("no-such-device@0", "info"), params=api.params()
        )
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_execute_command_nonexistent_device(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_mock(monkeypatch)
        response = await api.client.post(
            api.paas_device_url("no-such-device@0", "commands"),
            params=api.params(),
            json={"cmd": "echo hello"},
        )
        assert response.status_code == 200
