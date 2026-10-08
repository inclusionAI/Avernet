from __future__ import annotations

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.mock_paas_create_failure]


def _set_mock_failure(monkeypatch: pytest.MonkeyPatch, env_var: str) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv(env_var, "true")


class TestPaasFacadeCreateFailure:
    @pytest.mark.asyncio
    async def test_create_device_returns_500_with_mock_create_failure(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock_failure(monkeypatch, "PAAS_MOCK_CREATE_FAILURE")
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
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock_failure(monkeypatch, "PAAS_MOCK_CREATE_FAILURE")
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
