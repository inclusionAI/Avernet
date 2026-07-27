from __future__ import annotations

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    ASYNC_POLL_TIMEOUT,
    APITestHelper,
    activate_bot,
    approve_and_complete,
    approve_publish,
    create_test_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.mock_paas_destroy_failure]


def _set_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv("PAAS_MOCK_DESTROY_FAILURE", "true")


class TestDestroyDeviceFailure:
    @pytest.mark.asyncio
    async def test_destroy_device_fails(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_test_bot(api, f"destroy-fail-{unique_id}", device_count=1)
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
        assert status == "FAILED", f"Expected FAILED, got {status}"
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for d in devices:
            assert d.get("result_status") == "FAILED"
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        assert resp.status_code == 404
        device_resp = await api.client.get(
            f"/api/v1/bots/{bot['bot_uuid']}/devices", params=api.params()
        )
        if device_resp.status_code != 404:
            data = device_resp.json().get("data") or {}
            for d in data.get("items") or []:
                assert d.get("status") == "RELEASED"

    @pytest.mark.asyncio
    async def test_destroy_device_fails_multi_device(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_test_bot(
            api, f"destroy-fail-multi-{unique_id}", device_count=2
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
        assert status == "FAILED", f"Expected FAILED, got {status}"


class TestScaleDownDestroyFailure:
    @pytest.mark.asyncio
    async def test_scale_down_destroy_fails(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_test_bot(api, f"scaledn-fail-{unique_id}", device_count=3)
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
        assert status == "FAILED", f"Expected FAILED, got {status}"


class TestRestartDestroyPhaseFailure:
    @pytest.mark.asyncio
    async def test_restart_destroy_phase_fails_but_create_succeeds(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_test_bot(
            api, f"restart-destroy-fail-{unique_id}", device_count=1
        )
        await activate_bot(api, bot, timeout_seconds=ASYNC_POLL_TIMEOUT)
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/restart",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200
        publish_id = resp.json()["data"].get("publish_id")
        if not publish_id:
            pytest.skip("No publish_id returned from restart")
        status = await approve_and_complete(api, publish_id)
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"
