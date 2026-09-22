from __future__ import annotations

import pytest

from secbaas.community.core.service.paas._mock_paas_service import (
    reset_create_failure_counter,
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


def _set_mock(monkeypatch: pytest.MonkeyPatch, fail_times: int) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.delenv("PAAS_MOCK_CREATE_FAILURE", raising=False)
    monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", str(fail_times))
    reset_create_failure_counter()


class TestCreateDeviceRetry:
    @pytest.mark.asyncio
    async def test_first_attempt_fails_then_retry_succeeds(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch, fail_times=1)
        bot = await create_test_bot(
            api,
            f"retry-success-{unique_id}",
            publish_max_retry_times=2,
        )
        publish_id = bot["publish_id"]

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "SUCCESS", f"Expected SUCCESS after retry, got {status}"

        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1

    @pytest.mark.asyncio
    async def test_retry_exhausted_still_fails(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch, fail_times=99)
        bot = await create_test_bot(
            api,
            f"retry-exhaust-{unique_id}",
            publish_max_retry_times=1,
        )
        publish_id = bot["publish_id"]

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED"

        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for d in devices:
            assert d.get("result_status") == "FAILED"

    @pytest.mark.asyncio
    async def test_zero_budget_does_not_retry(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _set_mock(monkeypatch, fail_times=1)
        bot = await create_test_bot(
            api,
            f"no-retry-{unique_id}",
            publish_max_retry_times=0,
        )
        publish_id = bot["publish_id"]

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED"
