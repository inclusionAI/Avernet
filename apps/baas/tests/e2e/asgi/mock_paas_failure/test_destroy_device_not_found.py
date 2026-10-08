from __future__ import annotations

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    ASYNC_POLL_TIMEOUT,
    APITestHelper,
    activate_bot,
    approve_publish,
    create_test_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.mock_paas_device_not_found]


def _set_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv("PAAS_MOCK_DEVICE_NOT_FOUND", "true")


class TestDestroyDeviceNotFound:
    @pytest.mark.asyncio
    async def test_destroy_not_found_is_success(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_test_bot(
            api, f"destroy-notfound-{unique_id}", device_count=1
        )
        await activate_bot(api, bot, timeout_seconds=ASYNC_POLL_TIMEOUT)
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/destroy",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200
        publish_id = resp.json()["data"].get("publish_id")
        if not publish_id:
            pytest.skip("No publish_id returned from destroy")
        code = await approve_publish(api, publish_id)
        assert code == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "SUCCESS", f"Expected SUCCESS (idempotent), got {status}"
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for d in devices:
            assert d.get("result_status") == "SUCCESS"

    @pytest.mark.asyncio
    async def test_destroy_not_found_multi_device(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_test_bot(
            api, f"destroy-notfound-multi-{unique_id}", device_count=2
        )
        await activate_bot(api, bot, timeout_seconds=ASYNC_POLL_TIMEOUT)
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/destroy",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200
        publish_id = resp.json()["data"].get("publish_id")
        if not publish_id:
            pytest.skip("No publish_id returned from destroy")
        code = await approve_publish(api, publish_id)
        assert code == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "SUCCESS", f"Expected SUCCESS (idempotent), got {status}"


class TestScaleDownDeviceNotFound:
    @pytest.mark.asyncio
    async def test_scale_down_not_found_is_success(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_test_bot(
            api, f"scaledn-notfound-{unique_id}", device_count=3
        )
        await activate_bot(api, bot, timeout_seconds=ASYNC_POLL_TIMEOUT)
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/scale",
            params=api.params(),
            json={
                "target_count": 1,
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code == 200
        publish_id = resp.json()["data"].get("publish_id")
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")
        code = await approve_publish(api, publish_id)
        assert code == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "SUCCESS", f"Expected SUCCESS (idempotent), got {status}"
