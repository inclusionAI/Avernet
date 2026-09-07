"""E2E test for the TeClaw submit-fail branch.

Mirrors `test_create_device_failure.py` (ARCA) but uses `TEMPLATE_TECLAW` and
monkeypatches `StubTeClawBotPlugin.create_bot` to raise, since the TeClaw stub
has no `PAAS_MOCK_CREATE_FAILURE`-style env-var switch (its SPI is distinct
from the mocked `PaasService`).

Coverage gap filled:
  Existing `test_teclaw_async_publish_flow.py::test_teclaw_async_publish_failure`
  triggers failure via the async callback (success=False) AFTER submit succeeds.
  This test triggers the failure during submit itself:
    - `StubTeClawBotPlugin.create_bot` raises `PaasError(DEVICE_CREATION_FAILED)`
    - `_device_service.start_device` Step 10 except handler catches it,
      marks device status = FAILED
    - `_publish_service._execute_create_batch` sees the returned DeviceResponse
      with status=FAILED, marks publish_record result_status = FAILED
    - `execute_stage` transitions publish status = FAILED and bot = FAILED

Asserts both end states: publish_record result_status=FAILED, device
status=FAILED, publish status=FAILED, bot status=FAILED.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.plugins.bot.teclaw import StubTeClawBotPlugin
from tests.e2e.asgi.conftest import (
    ASYNC_POLL_TIMEOUT,
    APITestHelper,
    TEMPLATE_TECLAW,
    approve_publish,
    create_test_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

if TYPE_CHECKING:
    pass

pytestmark = [pytest.mark.mock_paas_create_failure]


async def _raise_create_bot(
    self: StubTeClawBotPlugin,
    bot_config: dict[str, Any],
    *,
    callback_context: Any = None,
) -> Any:
    raise PaasError(ErrorCode.DEVICE_CREATION_FAILED, "teclaw submit-fail mock")


class TestTeClawSubmitFailure:
    @pytest.mark.asyncio
    async def test_teclaw_create_bot_submit_fail(
        self,
        api: APITestHelper,
        monkeypatch: pytest.MonkeyPatch,
        unique_id: str,
    ) -> None:
        monkeypatch.setattr(
            StubTeClawBotPlugin, "create_bot", _raise_create_bot
        )

        bot = await create_test_bot(
            api, f"teclaw-submit-fail-{unique_id}", template_uuid=TEMPLATE_TECLAW
        )
        publish_id = bot["publish_id"]
        try:
            code = await approve_publish(api, publish_id)
            assert code == 200, f"Approve failed: {code}"

            status = await wait_for_publish_status(
                api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
            )
            assert status == "FAILED", f"Expected FAILED, got {status}"

            devices = await get_devices_from_progress(api, publish_id)
            assert len(devices) >= 1, "Expected at least one device in progress"
            for d in devices:
                assert d.get("result_status") == "FAILED", (
                    f"Device result_status should be FAILED; got: {d.get('result_status')}"
                )

            resp = await api.client.get(
                api.bot_url(bot["bot_uuid"]), params=api.params()
            )
            if resp.status_code == 200:
                assert resp.json()["data"]["status"] == "FAILED", (
                    f"Bot should be FAILED after CREATE publish failure; "
                    f"got: {resp.json()['data'].get('status')}"
                )
        finally:
            from tests.e2e.asgi.conftest import cleanup_bot

            await cleanup_bot(api, bot["bot_uuid"])


class TestTeClawSubmitFailureMultiDevice:
    @pytest.mark.asyncio
    async def test_teclaw_create_bot_submit_fail_multi_device(
        self,
        api: APITestHelper,
        monkeypatch: pytest.MonkeyPatch,
        unique_id: str,
    ) -> None:
        monkeypatch.setattr(
            StubTeClawBotPlugin, "create_bot", _raise_create_bot
        )

        bot = await create_test_bot(
            api,
            f"teclaw-submit-fail-multi-{unique_id}",
            template_uuid=TEMPLATE_TECLAW,
            device_count=3,
        )
        publish_id = bot["publish_id"]
        try:
            code = await approve_publish(api, publish_id)
            assert code == 200, f"Approve failed: {code}"

            status = await wait_for_publish_status(
                api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
            )
            assert status == "FAILED", f"Expected FAILED, got {status}"

            devices = await get_devices_from_progress(api, publish_id)
            assert len(devices) >= 1, "Expected at least one device in progress"
            for d in devices:
                assert d.get("result_status") == "FAILED", (
                    f"Multi-device: result_status should be FAILED; "
                    f"got: {d.get('result_status')}"
                )
        finally:
            from tests.e2e.asgi.conftest import cleanup_bot

            await cleanup_bot(api, bot["bot_uuid"])