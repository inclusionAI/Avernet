"""E2E tests for admin force-success API.

Test scenario: create a hook bot, approve it, send FAILED callbacks,
then use the force-success admin API to override all states to success.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import time

import pytest

from ..conftest import APITestHelper, cleanup_bot
from ..hook_helpers import (
    _ColorLogger,
    approve_publish,
    create_hook_bot,
    send_callbacks_for_hook_devices,
    wait_for_publish_status,
)

log = _ColorLogger(__import__("logging").getLogger("e2e.admin_force_success"))

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


async def call_force_success(
    api: APITestHelper, publish_id: int, modifier: str = "e2e-test"
) -> dict:
    """Call the admin force-success endpoint and return response data."""
    log.info(f"[FORCE_SUCCESS] calling force-success for publish_id={publish_id}")
    resp = await api.client.post(
        api.admin_force_success_url(),
        params=api.params(),
        json={"publish_id": publish_id, "modifier": modifier},
    )
    assert resp.status_code == 200, (
        f"[FORCE_SUCCESS] failed: {resp.status_code} {resp.text}"
    )
    data = resp.json()["data"]
    log.info(
        f"[FORCE_SUCCESS] publish_id={publish_id} previous={data['previous_publish_status']} "
        f"batches={data['batches_updated']} records={data['records_updated']} "
        f"devices={data['devices_updated']} bot_updated={data['bot_updated']}"
    )
    return data


class TestForceSuccessAfterCallbackFailure:
    """CREATE with hook: approve → callback FAILED → force-success → all states correct."""

    @pytest.mark.asyncio
    async def test_force_success_after_failed_callback(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        t0 = time.monotonic()

        bot = await create_hook_bot(api, f"force-success-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]
        log.info(f"created bot={bot['bot_uuid']} publish_id={publish_id}")

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=1,
            stderr="simulated failure",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED", "APPROVING"}, timeout_seconds=0.5
        )
        log.info(f"publish status after failed callbacks: {status}")

        result = await call_force_success(api, publish_id)

        assert result["publish_id"] == publish_id
        assert result["previous_publish_status"] in (
            "FAILED",
            "ACTIVE",
            "APPROVING",
        )
        assert result["batches_updated"] >= 0
        assert result["records_updated"] >= 0
        assert result["bot_updated"] is True

        status_after = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status_after == "SUCCESS", (
            f"Expected SUCCESS after force, got {status_after}"
        )

        resp = await api.client.get(api.publish_url(publish_id), params=api.params())
        assert resp.status_code == 200
        publish_data = resp.json()["data"]
        assert publish_data["status"] == "SUCCESS"

        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200

        log.info(
            f"[TIMING] test_force_success_after_failed_callback: "
            f"{time.monotonic() - t0:.2f}s"
        )

        await cleanup_bot(api, bot["bot_uuid"])


class TestForceSuccessNotFound:
    """Force-success with non-existent publish_id returns 404."""

    @pytest.mark.asyncio
    async def test_force_success_publish_not_found(self, api: APITestHelper) -> None:
        resp = await api.client.post(
            api.admin_force_success_url(),
            params=api.params(),
            json={"publish_id": 99999999, "modifier": "e2e-test"},
        )
        assert resp.status_code == 404


class TestForceSuccessIdempotent:
    """Force-success on an already-successful publish is idempotent."""

    @pytest.mark.asyncio
    async def test_force_success_idempotent(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        t0 = time.monotonic()

        bot = await create_hook_bot(api, f"force-idem-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]

        await approve_publish(api, publish_id)
        await send_callbacks_for_hook_devices(api, publish_id)
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS"

        result = await call_force_success(api, publish_id)

        assert result["publish_id"] == publish_id
        assert result["previous_publish_status"] == "SUCCESS"
        assert result["batches_updated"] == 0
        assert result["records_updated"] == 0

        status_after = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status_after == "SUCCESS"

        log.info(
            f"[TIMING] test_force_success_idempotent: {time.monotonic() - t0:.2f}s"
        )

        await cleanup_bot(api, bot["bot_uuid"])
