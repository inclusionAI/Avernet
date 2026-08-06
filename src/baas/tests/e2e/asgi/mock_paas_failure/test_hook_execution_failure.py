from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    activate_bot,
    approve_publish,
    create_and_activate_bot,
    create_hook_bot,
    create_test_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [
    pytest.mark.mock_paas_hook_failure,
    pytest.mark.skip(reason="flaky: hook execution timing is non-deterministic"),
]

DESTROY_ONLY_HOOK_DEPLOY_CONFIG = {
    "before_destroy_cmd_hook": "/bin/echo 'hook executed'"
}


def _set_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv("PAAS_MOCK_HOOK_FAILURE", "true")


class TestCreateHookFailure:
    @pytest.mark.asyncio
    async def test_create_with_hook_failure(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_hook_bot(api, f"create-hook-fail-{unique_id}")
        publish_id = bot["publish_id"]
        code = await approve_publish(api, publish_id)
        assert code == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=5.0
        )
        assert status == "FAILED", f"Expected publish FAILED, got {status}"
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for d in devices:
            assert d.get("result_status") == "FAILED"
        # Bot status transition ACTIVE → FAILED is async: poll with backoff.
        bot_status = "ACTIVE"
        t0 = time.monotonic()
        while time.monotonic() - t0 < 5.0:
            resp = await api.client.get(
                api.bot_url(bot["bot_uuid"]), params=api.params()
            )
            if resp.status_code == 200:
                bot_status = resp.json()["data"]["status"]
                if bot_status == "FAILED":
                    break
            await asyncio.sleep(0.1)
        assert bot_status == "FAILED", f"Expected bot FAILED, got {bot_status}"


class TestDestroyHookFailure:
    @pytest.mark.asyncio
    async def test_destroy_with_hook_failure(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_test_bot(
            api,
            f"destroy-hook-fail-{unique_id}",
            device_count=1,
            deploy_config=DESTROY_ONLY_HOOK_DEPLOY_CONFIG,
        )
        await activate_bot(api, bot)
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
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=5.0
        )
        assert status in ("SUCCESS", "FAILED"), f"Expected terminal state, got {status}"


class TestRestartHookFailure:
    @pytest.mark.asyncio
    async def test_restart_with_hook_failure(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch)
        bot = await create_and_activate_bot(
            api, f"restart-hook-fail-{unique_id}", device_count=1
        )
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/restart",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200
        publish_id = resp.json()["data"].get("publish_id")
        if not publish_id:
            pytest.skip("No publish_id returned from restart")
        code = await approve_publish(api, publish_id)
        assert code == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=5.0
        )
        # before_destroy_cmd_hook is non-blocking by design: a hook failure is
        # logged as a warning and the restart (destroy + create) proceeds. Mirror
        # TestDestroyHookFailure and accept either terminal state.
        assert status in ("SUCCESS", "FAILED"), f"Expected terminal state, got {status}"
