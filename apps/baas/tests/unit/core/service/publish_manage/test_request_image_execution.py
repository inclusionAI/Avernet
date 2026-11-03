"""Deployment image snapshots must survive execution and retry boundaries."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.device_manage import DeployConfig
from secbaas.community.core.service.publish_manage import DefaultPublishService


def _service(image="old:image", snapshot=None):
    device = SimpleNamespace(
        id=10,
        device_uuid="device-image-test",
        extra_config={
            "template_uuid": "template-image-test",
            "deploy_config": {"docker_image": image, "envs": {"KEEP": "yes"}},
        },
    )
    record = SimpleNamespace(id=20, device_id=10, device_uuid=device.device_uuid)
    bot = SimpleNamespace(
        id=1,
        template_uuid="template-image-test",
        extra_config={
            "deploy_config": {"docker_image": "old:bot", "envs": {"KEEP": "yes"}},
        },
    )
    publish = SimpleNamespace(id=30, bot_id=1, extra_config=snapshot or {})
    device_repo, records, bot_repo = MagicMock(), MagicMock(), MagicMock()
    device_repo.get_by_ids.return_value = {10: device}
    records.list_by_publish_id_and_batch_id.return_value = [record]
    bot_repo.get_by_id.return_value = bot
    service = DefaultPublishService(
        bot_repo=bot_repo,
        device_repo=device_repo,
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=MagicMock(),
        batch_repo=MagicMock(),
        publish_record_repo=records,
        template_service=MagicMock(),
        bot_service=MagicMock(),
        device_service=MagicMock(),
    )
    service._drain_device = AsyncMock(return_value=SimpleNamespace(success=True))
    service._drive_record_attempt = AsyncMock(return_value=True)
    service._device_service.update_device = AsyncMock(
        return_value=SimpleNamespace(status="ACTIVE")
    )
    service._device_service.restart_device = AsyncMock(
        return_value=SimpleNamespace(status="ACTIVE")
    )
    batch = SimpleNamespace(id=40, batch_index=0, batch_capacity=1)
    return service, device, publish, batch


async def _execute(service, publish, batch, operation):
    return await getattr(service, f"_execute_{operation}_batch")(
        tenant="test",
        publish_id=30,
        batch=batch,
        drain_timeout=0,
        operator="test",
        publish_record=publish,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("image", [None, "new:image"])
async def test_update_consumer_uses_publish_snapshot_not_old_bot_image(image):
    snapshot = {"deploy_config": {"docker_image": image, "envs": {"KEEP": "yes"}}}
    service, device, publish, batch = _service(snapshot=snapshot)
    old = deepcopy(device.extra_config)

    async def provision(**kwargs):
        written = service._device_repo.update_device.call_args.kwargs["extra_config"]
        assert written["deploy_config"].get("docker_image") == image
        assert written["deploy_config"]["envs"] == {"KEEP": "yes"}
        return SimpleNamespace(status="ACTIVE")

    service._device_service.update_device.side_effect = provision
    result = await _execute(service, publish, batch, "update")
    assert result.success and result.processed_count == 1
    assert device.extra_config == old
    service._drive_record_attempt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("image", [None, "new:image"])
@pytest.mark.parametrize("write_failure", ["exception", "zero-rows"])
async def test_update_config_write_failure_never_enters_provider_retry(
    image, write_failure
):
    service, device, publish, batch = _service(
        snapshot={"deploy_config": {"docker_image": image}}
    )
    if write_failure == "exception":
        service._device_repo.update_device.side_effect = RuntimeError(
            "write unavailable"
        )
    else:
        service._device_repo.update_device.return_value = 0
    result = await _execute(service, publish, batch, "update")
    assert not result.success and result.failed_count == 1
    service._device_service.update_device.assert_not_awaited()
    service._device_service.restart_device.assert_not_awaited()
    service._drive_record_attempt.assert_not_awaited()
    assert (
        service._publish_record_repo.update_result.call_args.kwargs["result_status"]
        == "FAILED"
    )
    assert device.extra_config["deploy_config"]["docker_image"] == "old:image"


@pytest.mark.asyncio
async def test_native_restart_keeps_existing_image_and_provider_retry_behavior():
    service, device, publish, batch = _service()
    original = deepcopy(device.extra_config)
    service._device_service.restart_device.side_effect = RuntimeError(
        "provider unavailable"
    )

    result = await _execute(service, publish, batch, "restart")

    assert result.success  # Existing retry policy is unchanged.
    service._device_repo.update_device.assert_not_called()
    service._drive_record_attempt.assert_awaited_once()
    assert device.extra_config == original


@pytest.mark.asyncio
async def test_native_restart_keeps_existing_persisted_image_override():
    service, device, publish, batch = _service()
    original = deepcopy(device.extra_config)

    result = await _execute(service, publish, batch, "restart")

    assert result.success
    service._device_repo.update_device.assert_not_called()
    service._device_service.restart_device.assert_awaited_once()
    assert device.extra_config == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot,expected_deploy",
    [
        ({}, {"docker_image": "old:bot", "envs": {"KEEP": "yes"}}),
        ({"deploy_config": None}, None),
    ],
)
async def test_explicit_null_snapshot_is_distinct_from_legacy_missing_key(
    snapshot, expected_deploy
):
    service, device, publish, batch = _service(snapshot=snapshot)

    result = await _execute(service, publish, batch, "update")

    assert result.success
    written = service._device_repo.update_device.call_args.kwargs["extra_config"]
    assert written.get("deploy_config") == (
        DeployConfig.model_validate(expected_deploy).model_dump(exclude_none=True)
        if expected_deploy is not None
        else None
    )
