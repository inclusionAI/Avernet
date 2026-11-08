"""E2E: DESTROY must tear down every replica, not just one.

Regression for the terminal-publish device-count defect: DESTROY and STOP
carried no replica_desired, so `_calculate_total_devices` fell back to 1 and
`_create_device_records_for_publish` sliced the eligible-device list down to a
single device. The bot was then soft-deleted while the remaining sandboxes
stayed ACTIVE on the provider.

The pre-existing destroy e2e uses device_count=1, which is exactly the shape
where the defect is invisible (`destroy 1` == `destroy all`).

Assertions use the publish progress endpoint rather than
`GET /bots/{uuid}/devices`, because the bot is soft-deleted by the time the
destroy completes and the bot-scoped device list then returns BOT_NOT_FOUND.

Requires the in-process ASGI transport with stub PaaS (e2e_asgi marker).
"""

import logging
import time
import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    activate_bot,
    cleanup_bot,
    create_test_bot,
    wait_for_publish_status,
)

tlog = logging.getLogger("e2e.destroy_all_replicas")

pytestmark = [pytest.mark.e2e_asgi]


async def _destroy_publish_progress(api: APITestHelper, publish_id: int) -> dict:
    """Fetch publish progress including per-device destroy records."""
    resp = await api.client.get(
        api.publish_url(publish_id) + "/progress",
        params={**api.params(), "include_devices": "true"},
    )
    assert resp.status_code == 200
    return resp.json()["data"]


def _destroyed_device_ids(progress: dict) -> list[int]:
    """Collect device ids that carry a DESTROY record in this publish."""
    ids: list[int] = []
    for batch in progress.get("device_details") or []:
        for record in batch.get("devices") or []:
            if record.get("event_type") != "DESTROY":
                continue
            if record.get("result_status") not in ("SUCCESS", "PROCESSING"):
                continue
            device_id = record.get("old_device_id") or record.get("device_id")
            if device_id is not None:
                ids.append(int(device_id))
    return ids


class TestDestroyAllReplicas:
    """DESTROY covers every replica of a multi-device bot."""

    @pytest.mark.asyncio
    async def test_destroy_multi_replica_bot_retires_every_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """A 3-replica bot produces three DESTROY records, not one."""
        t0 = time.monotonic()
        replica_count = 3
        bot = await create_test_bot(
            api, f"destroy-all-{unique_id}", device_count=replica_count
        )
        bot_uuid = bot["bot_uuid"]

        await activate_bot(api, bot)
        await wait_for_publish_status(
            api, bot["publish_id"], {"SUCCESS"}, timeout_seconds=5.0
        )

        resp = await api.client.post(
            api.bot_url(bot_uuid) + "/destroy",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "auto_approve_publish": True,
            },
        )
        assert resp.status_code == 200
        destroy_publish_id = resp.json()["data"].get("publish_id")
        assert destroy_publish_id, "destroy did not return a publish_id"

        final_status = await wait_for_publish_status(
            api, destroy_publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=5.0
        )
        assert final_status == "SUCCESS", (
            f"destroy publish {destroy_publish_id} ended {final_status}"
        )

        progress = await _destroy_publish_progress(api, destroy_publish_id)

        # The defect surfaced here: the plan covered a single device, so
        # total_devices was 1 for a 3-replica bot and the other replicas were
        # never destroyed.
        overall = progress.get("overall_progress") or {}
        assert overall.get("total_devices") == replica_count, (
            f"DESTROY planned {overall.get('total_devices')} of "
            f"{replica_count} replicas: {progress.get('device_details')}"
        )

        destroyed = set(_destroyed_device_ids(progress))
        assert len(destroyed) == replica_count, (
            f"DESTROY retired {len(destroyed)} of {replica_count} replicas "
            f"(ids={sorted(destroyed)}), leaving the rest ACTIVE"
        )

        tlog.info(f"[TIMING] destroy-all-replicas: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot_uuid)
