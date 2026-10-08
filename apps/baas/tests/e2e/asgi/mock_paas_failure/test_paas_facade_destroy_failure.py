from __future__ import annotations

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.mock_paas_destroy_failure]


def _set_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv("PAAS_MOCK_DESTROY_FAILURE", "true")


class TestPaasFacadeDestroyFailure:
    @pytest.mark.asyncio
    async def test_destroy_device_returns_error_with_mock_destroy_failure(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_mock(monkeypatch)
        response = await api.client.delete(
            api.paas_device_url("test-device@1"), params=api.params()
        )
        assert response.status_code in (400, 404, 500, 503)

    @pytest.mark.asyncio
    async def test_destroy_device_with_suffix_fails(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_mock(monkeypatch)
        response = await api.client.delete(
            api.paas_device_url("test-device@0"), params=api.params()
        )
        assert response.status_code in (400, 404, 500, 503)

    @pytest.mark.asyncio
    async def test_idempotent_destroy_still_reports_failure(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_mock(monkeypatch)
        response = await api.client.delete(
            api.paas_device_url("already-destroyed@1"), params=api.params()
        )
        assert response.status_code in (400, 404, 500, 503)
