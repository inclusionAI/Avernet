"""FAILED online targets reuse their existing UPDATE workflow and binding."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from tests.community.core.service_bot.services.test_publish_flow_service import (
    _arca_router,
    _make_publish_record,
    _pf,
    _setup_restart,
)


@pytest.mark.asyncio
async def test_failed_teclaw_online_retry_updates_existing_candidate_and_logs_decision():
    publish_service = Mock()
    build_service = Mock()
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 77})
    build_service.release_async = AsyncMock(
        return_value={"publish_id": 88, "bot_uuid": "BOT-unwanted-replacement"}
    )
    baas_service = Mock()
    baas_service.get_bot.return_value = {"status": "FAILED"}
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))
    record = _make_publish_record(
        status=PublishStatus.ONLINE_PUB.value,
        last_pub_id=10,
        ext={"binding": {"online": 99}, "config_artifact": {"skills": []}},
    )
    publish_service.get_device_binding_by_id.return_value = Mock(device_id="BOT-failed")
    svc._bot_service = Mock()
    svc._bot_service.get_bot_by_address.return_value = {"bot_id": "bot-source"}
    svc._ext_state.get_latest_ext = Mock(return_value=record.ext)
    svc._ext_state.update_status = Mock()
    svc._release_binding = Mock()

    with patch(
        "agentclaw.community.core.service_bot.services.publish_flow.upgrade_resolution_mixin.logger"
    ) as logger:
        result = await svc.execute_release_phase(record, operator="op")

    assert result.status == PublishStatus.ONLINE_PUB
    assert result.bot_uuid == "BOT-failed"
    assert result.device_binding_id == 99
    assert result.baas_publish_id == "77"
    build_service.upgrade_async.assert_awaited_once()
    assert build_service.upgrade_async.await_args.kwargs["bot_uuid"] == "BOT-failed"
    assert build_service.upgrade_async.await_args.kwargs["delivery"].config_artifact["skills"] == []
    build_service.retire_superseded_bot.assert_not_called()
    build_service.release_async.assert_not_awaited()
    svc._release_binding.assert_not_called()
    publish_service.create_device_binding.assert_not_called()
    assert record.ext["binding"]["online"] == 99
    # The current failed attempt wins over the previous version's candidate.
    publish_service.get_publish_by_id.assert_not_called()
    messages = " ".join(str(call) for call in logger.info.call_args_list)
    assert "bot_uuid=BOT-failed" in messages
    assert "status=FAILED" in messages
    assert "provider=teclaw -> upgrade" in messages


@pytest.mark.asyncio
async def test_failed_teclaw_online_restart_updates_without_retirement_or_recreation():
    publish_service = Mock()
    build_service = Mock()
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 77})
    build_service.release_async = AsyncMock(
        return_value={"publish_id": 88, "bot_uuid": "BOT-unwanted-replacement"}
    )
    baas_service = Mock()
    baas_service.get_bot.return_value = {"status": "FAILED"}
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))
    record = _make_publish_record(
        status=PublishStatus.SUCCESS.value,
        ext={"binding": {"online": 1}, "config_artifact": {"skills": []}},
    )
    _setup_restart(svc, record, bot_uuid="BOT-failed")
    svc._release_binding = Mock()

    result = await svc.execute_restart(publish_id=1, stage="online", operator="op")

    assert result["success"] is True
    build_service.upgrade_async.assert_awaited_once()
    assert build_service.upgrade_async.await_args.kwargs["bot_uuid"] == "BOT-failed"
    build_service.retire_superseded_bot.assert_not_called()
    build_service.release_async.assert_not_awaited()
    svc._release_binding.assert_not_called()
    publish_service.create_device_binding.assert_not_called()
