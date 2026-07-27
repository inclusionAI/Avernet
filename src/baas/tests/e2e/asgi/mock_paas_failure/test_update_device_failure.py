from __future__ import annotations

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    ASYNC_POLL_TIMEOUT,
    APITestHelper,
    cleanup_bot,
    create_and_activate_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.mock_paas_create_failure]


def _set_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.setenv("PAAS_MOCK_CREATE_FAILURE", "true")


class TestUpdateDeviceFailure:
    @pytest.mark.asyncio
    async def test_update_device_publish_fails_on_device_create(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        """UPDATE_DEVICE: create_device fails → publish FAILED, bot status unchanged.

        Creates and activates the bot WITHOUT mock (so devices exist),
        THEN enables mock failure before calling update-devices.
        This avoids the chicken-and-egg where the initial activation
        produces 0 devices due to the mock failure.
        """
        # Create and activate without mock — need real devices for the test.
        bot = await create_and_activate_bot(
            api, f"upd-dev-fail-{unique_id}", device_count=1
        )
        bot_uuid = bot["bot_uuid"]

        resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        assert resp.status_code == 200
        bot_data = resp.json()["data"]
        original_status = bot_data["status"]

        # Devices may not appear on the bot detail endpoint immediately
        # after activation (ASGI in-process timing). Use the publish progress
        # which is the canonical source of device records.
        publish_id_initial = bot["publish_id"]
        devices = await get_devices_from_progress(api, publish_id_initial)
        if not devices:
            await cleanup_bot(api, bot_uuid)
            pytest.skip(
                f"No devices found via publish {publish_id_initial} for bot {bot_uuid}"
            )

        # Now enable the mock failure — update-devices recreates devices
        # and the create step should fail.
        _set_mock(monkeypatch)

        device_uuid = devices[0]["device_uuid"]
        resp = await api.client.post(
            f"{api.bot_url(bot_uuid)}/update-devices",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "device_uuids": [device_uuid],
                "auto_approve_publish": True,
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code in (200, 409)
        if resp.status_code == 409:
            await cleanup_bot(api, bot_uuid)
            pytest.skip("Bot has concurrent active publish")
        data = resp.json()
        assert data["code"] == 0
        publish_id = data["data"]["publish_id"]
        assert publish_id > 0
        assert data["data"]["status"] == original_status
        publish_status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert publish_status == "FAILED", f"Expected FAILED, got {publish_status}"
        devices_result = await get_devices_from_progress(api, publish_id)
        if devices_result:
            assert all(d["result_status"] == "FAILED" for d in devices_result)
        await cleanup_bot(api, bot_uuid)
