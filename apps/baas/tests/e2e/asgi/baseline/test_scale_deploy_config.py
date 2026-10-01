"""E2E tests for scale deploy_config flow.

Verifies that new devices created during SCALE_UP receive the
deploy_config from the Scale Publish request, not the bot's
persisted extra_config.
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    approve_publish,
    cleanup_bot,
    create_and_activate_bot,
    send_callbacks_for_hook_devices,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e_asgi]


async def _scale_up_bot_with_config(
    api: APITestHelper,
    bot_uuid: str,
    target_count: int,
    deploy_config: dict,
) -> int | None:
    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/scale",
        params=api.params(),
        json={
            "target_count": target_count,
            "operator": "e2e-test",
            "request_id": uuid.uuid4().hex,
            "config": {"deploy_config": deploy_config},
        },
    )
    assert resp.status_code == 200
    return resp.json()["data"].get("publish_id")


class TestScaleDeployConfig:
    """New devices from SCALE_UP use the scale request's deploy_config."""

    @pytest.mark.asyncio
    async def test_scale_up_new_device_gets_publish_deploy_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        bot = await create_and_activate_bot(
            api, f"scale-dc-{unique_id}", device_count=1
        )
        scale_dc = {
            "docker_image": "test-image:v2",
            "ttl_in_minutes": 999,
            "envs": {"SCALE_KEY": "scale_value"},
        }
        publish_id = await _scale_up_bot_with_config(api, bot["bot_uuid"], 2, scale_dc)
        if not publish_id:
            pytest.skip("No publish_id returned from scale up")

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_callbacks_for_hook_devices(api, publish_id)

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        resp = await api.client.get(
            api.publish_url(publish_id, "progress"),
            params=api.params(include_devices="true"),
        )
        assert resp.status_code == 200
        progress = resp.json()["data"]

        devices = []
        for batch_detail in progress.get("device_details", []):
            devices.extend(batch_detail.get("devices", []))

        new_device = next(
            (d for d in devices if d.get("result_status") in ("SUCCESS", "PENDING")),
            None,
        )
        assert new_device is not None, "No new device found in publish progress"

        device_uuid = new_device.get("device_uuid")
        assert device_uuid is not None

        detail_resp = await api.client.get(
            api.device_url(device_uuid), params=api.params()
        )
        assert detail_resp.status_code == 200
        detail = detail_resp.json()["data"]
        device_deploy_config = detail.get("extra_config", {}).get("deploy_config") or {}

        assert device_deploy_config.get("docker_image") == "test-image:v2"
        assert device_deploy_config.get("ttl_in_minutes") == 999
        assert device_deploy_config.get("envs") == {"SCALE_KEY": "scale_value"}

        await cleanup_bot(api, bot["bot_uuid"])
