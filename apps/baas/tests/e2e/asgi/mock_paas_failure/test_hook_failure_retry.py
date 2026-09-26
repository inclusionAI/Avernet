from __future__ import annotations

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
from tests.e2e.hook_helpers import (
    HOOK_DEPLOY_CONFIG,
    send_callbacks_for_hook_devices,
)

pytestmark = [pytest.mark.mock_paas_hook_failure]


async def _create_hook_bot(
    api: APITestHelper, name: str, *, publish_max_retry_times: int | None = None
):
    return await create_test_bot(
        api,
        name,
        deploy_config=HOOK_DEPLOY_CONFIG,
        device_count=1,
        publish_max_retry_times=publish_max_retry_times,
    )


class TestHookFailureRetry:
    @pytest.mark.asyncio
    async def test_hook_fails_once_then_retry_succeeds(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")
        monkeypatch.delenv("PAAS_MOCK_HOOK_FAILURE", raising=False)
        monkeypatch.setenv("PAAS_MOCK_HOOK_FAIL_TIMES", "1")
        reset_failure_counters()

        bot = await _create_hook_bot(
            api, f"hook-retry-{unique_id}", publish_max_retry_times=2
        )
        publish_id = bot["publish_id"]
        assert (await approve_publish(api, publish_id)) == 200

        await send_callbacks_for_hook_devices(api, publish_id)
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=5.0
        )
        assert status == "SUCCESS", f"Expected SUCCESS after hook retry, got {status}"

        devices = await get_devices_from_progress(api, publish_id)
        assert devices
        for d in devices:
            assert d.get("result_status") == "SUCCESS"

    @pytest.mark.asyncio
    async def test_hook_failure_exhausts_budget(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")
        monkeypatch.delenv("PAAS_MOCK_HOOK_FAILURE", raising=False)
        monkeypatch.setenv("PAAS_MOCK_HOOK_FAIL_TIMES", "99")
        reset_failure_counters()

        bot = await _create_hook_bot(
            api, f"hook-exhaust-{unique_id}", publish_max_retry_times=1
        )
        publish_id = bot["publish_id"]
        assert (await approve_publish(api, publish_id)) == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=5.0
        )
        assert status == "FAILED"

        devices = await get_devices_from_progress(api, publish_id)
        assert devices
        for d in devices:
            assert d.get("result_status") == "FAILED"

    @pytest.mark.asyncio
    async def test_hook_failure_without_budget_fails_immediately(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")
        monkeypatch.delenv("PAAS_MOCK_HOOK_FAILURE", raising=False)
        monkeypatch.setenv("PAAS_MOCK_HOOK_FAIL_TIMES", "1")
        reset_failure_counters()

        bot = await _create_hook_bot(
            api, f"hook-noretry-{unique_id}", publish_max_retry_times=0
        )
        publish_id = bot["publish_id"]
        assert (await approve_publish(api, publish_id)) == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=5.0
        )
        assert status == "FAILED"

    @pytest.mark.asyncio
    async def test_hook_retry_consumes_attempts(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")
        monkeypatch.delenv("PAAS_MOCK_HOOK_FAILURE", raising=False)
        monkeypatch.setenv("PAAS_MOCK_HOOK_FAIL_TIMES", "1")
        reset_failure_counters()

        bot = await _create_hook_bot(
            api, f"hook-attempts-{unique_id}", publish_max_retry_times=2
        )
        publish_id = bot["publish_id"]
        assert (await approve_publish(api, publish_id)) == 200
        await send_callbacks_for_hook_devices(api, publish_id)
        await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=5.0
        )

        devices = await get_devices_from_progress(api, publish_id)
        assert devices
        assert any(d.get("publish_retry_count", 0) >= 1 for d in devices), (
            "hook retry should record an attempt"
        )
