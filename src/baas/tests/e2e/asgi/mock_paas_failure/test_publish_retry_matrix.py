from __future__ import annotations

import uuid

import pytest

from secbaas.community.core.service.paas._mock_paas_service import (
    reset_failure_counters,
)
from tests.e2e.asgi.conftest import (
    ASYNC_POLL_TIMEOUT,
    APITestHelper,
    approve_publish,
    create_test_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.mock_paas_create_failure]


async def _restart_bot(
    api: APITestHelper,
    bot_uuid: str,
    *,
    publish_max_retry_times: int | None = None,
    scope: str = "all",
) -> int | None:
    body: dict = {
        "operator": "e2e-test",
        "request_id": uuid.uuid4().hex,
        "scope": scope,
    }
    if publish_max_retry_times is not None:
        body["publish_max_retry_times"] = publish_max_retry_times
    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/restart", params=api.params(), json=body
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"].get("publish_id")


async def _scale_up(
    api: APITestHelper,
    bot_uuid: str,
    target_count: int,
    *,
    publish_max_retry_times: int | None = None,
) -> int | None:
    config: dict = {}
    if publish_max_retry_times is not None:
        config["publish_max_retry_times"] = publish_max_retry_times
    body: dict = {
        "target_count": target_count,
        "operator": "e2e-test",
        "request_id": uuid.uuid4().hex,
    }
    if config:
        body["config"] = config
    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/scale", params=api.params(), json=body
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"].get("publish_id")


class TestScaleUpRetry:
    @pytest.mark.asyncio
    async def test_scale_up_first_attempt_fails_then_retry_succeeds(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")
        monkeypatch.delenv("PAAS_MOCK_CREATE_FAILURE", raising=False)
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "1")
        reset_failure_counters()

        bot = await create_test_bot(api, f"scale-retry-{unique_id}", device_count=1)
        await approve_publish(api, bot["publish_id"])
        await wait_for_publish_status(
            api,
            bot["publish_id"],
            {"SUCCESS", "FAILED"},
            timeout_seconds=ASYNC_POLL_TIMEOUT,
        )

        publish_id = await _scale_up(api, bot["bot_uuid"], 2, publish_max_retry_times=2)
        assert publish_id is not None
        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "SUCCESS", f"Expected SUCCESS after scale retry, got {status}"

    @pytest.mark.asyncio
    async def test_scale_up_retry_exhausted_fails(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "99")
        reset_failure_counters()

        bot = await create_test_bot(api, f"scale-exhaust-{unique_id}", device_count=1)
        await approve_publish(api, bot["publish_id"])
        await wait_for_publish_status(
            api,
            bot["publish_id"],
            {"SUCCESS", "FAILED"},
            timeout_seconds=ASYNC_POLL_TIMEOUT,
        )

        publish_id = await _scale_up(api, bot["bot_uuid"], 2, publish_max_retry_times=1)
        assert publish_id is not None
        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED"


class TestRestartRetry:
    @pytest.mark.asyncio
    async def test_restart_first_attempt_fails_then_retry_succeeds(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")
        monkeypatch.delenv("PAAS_MOCK_CREATE_FAILURE", raising=False)
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "1")
        reset_failure_counters()

        bot = await create_test_bot(api, f"restart-retry-{unique_id}", device_count=1)
        await approve_publish(api, bot["publish_id"])
        await wait_for_publish_status(
            api,
            bot["publish_id"],
            {"SUCCESS", "FAILED"},
            timeout_seconds=ASYNC_POLL_TIMEOUT,
        )

        publish_id = await _restart_bot(api, bot["bot_uuid"], publish_max_retry_times=2)
        assert publish_id is not None
        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "SUCCESS", (
            f"Expected SUCCESS after restart retry, got {status}"
        )

    @pytest.mark.asyncio
    async def test_restart_without_budget_fails_immediately(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "1")
        reset_failure_counters()

        bot = await create_test_bot(api, f"restart-noretry-{unique_id}", device_count=1)
        await approve_publish(api, bot["publish_id"])
        await wait_for_publish_status(
            api,
            bot["publish_id"],
            {"SUCCESS", "FAILED"},
            timeout_seconds=ASYNC_POLL_TIMEOUT,
        )
        reset_failure_counters()

        publish_id = await _restart_bot(api, bot["bot_uuid"])
        assert publish_id is not None
        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED"


class TestRetryThenCallbackSettles:
    @pytest.mark.asyncio
    async def test_retried_record_reaches_success_via_callback(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")
        monkeypatch.delenv("PAAS_MOCK_CREATE_FAILURE", raising=False)
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "1")
        reset_failure_counters()

        bot = await create_test_bot(
            api, f"retry-callback-{unique_id}", publish_max_retry_times=2
        )
        publish_id = bot["publish_id"]
        assert (await approve_publish(api, publish_id)) == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "SUCCESS"

        devices = await get_devices_from_progress(api, publish_id)
        assert devices
        for d in devices:
            assert d.get("result_status") != "PROCESSING", (
                "a retried record must not be left in flight"
            )

    @pytest.mark.asyncio
    async def test_retry_state_surfaced_in_progress(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "1")
        reset_failure_counters()

        bot = await create_test_bot(
            api, f"retry-progress-{unique_id}", publish_max_retry_times=2
        )
        publish_id = bot["publish_id"]
        assert (await approve_publish(api, publish_id)) == 200
        await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )

        devices = await get_devices_from_progress(api, publish_id)
        assert devices
        assert any(d.get("publish_retry_count", 0) >= 1 for d in devices), (
            "a retried device should report attempts consumed"
        )
