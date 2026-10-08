from __future__ import annotations

import pytest

from tests.e2e.asgi.conftest import (
    ASYNC_POLL_TIMEOUT,
    APITestHelper,
    approve_publish,
    create_hook_bot,
    create_test_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.mock_paas_create_failure]


def _set_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv("PAAS_MOCK_CREATE_FAILURE", "true")


class TestCreateDeviceFailure:
    @pytest.mark.asyncio
    async def test_create_device_fails(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_test_bot(api, f"create-fail-{unique_id}")
        publish_id = bot["publish_id"]
        code = await approve_publish(api, publish_id)
        assert code == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for d in devices:
            assert d.get("result_status") == "FAILED"
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        if resp.status_code == 200:
            assert resp.json()["data"]["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_create_device_fails_multi_device(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_test_bot(
            api, f"create-fail-multi-{unique_id}", device_count=3
        )
        publish_id = bot["publish_id"]
        code = await approve_publish(api, publish_id)
        assert code == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for d in devices:
            assert d.get("result_status") == "FAILED"


class TestCreateDeviceFailureWithHook:
    @pytest.mark.asyncio
    async def test_create_with_hook_device_failure(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_hook_bot(api, f"create-hook-devfail-{unique_id}")
        publish_id = bot["publish_id"]
        code = await approve_publish(api, publish_id)
        assert code == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for d in devices:
            assert d.get("result_status") == "FAILED"
