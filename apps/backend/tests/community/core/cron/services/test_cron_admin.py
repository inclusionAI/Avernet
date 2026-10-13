"""Disable-only contract: permissions, stored owner identity and failure receipts."""
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.cron.services.cron_admin import CronAdminService


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.cron.services.cron_admin.cron_admin",
        lambda: frozenset({"admin"}),
    )
    bots = Mock()
    bots.get_unique_by_id.return_value = {
        "bot_id": "b1", "owner_id": "owner", "owner_name": "Owner", "bot_type": "service",
    }
    relay = Mock()
    relay.update_cron = AsyncMock(return_value={"success": True, "data": {"enabled": False}})
    relay.list_all_crons = AsyncMock(return_value={"success": True, "data": []})
    return CronAdminService(bots, relay)


async def disable(service, **overrides):
    kwargs = dict(operator_id="admin", bot_id="b1", task_id="t1", runtime_stage="online", reason="incident")
    return await service.disable_task(**(kwargs | overrides))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["verify", "online"])
async def test_disable_uses_stored_owner_and_only_enabled_false(service, caplog, stage):
    result = await disable(service, runtime_stage=stage)
    assert result["success"] is True
    service._relay.update_cron.assert_awaited_once_with(
        bot_id="b1", user_id="owner", nick_name="Owner", task_id="t1",
        body={"enabled": False}, runtime_stage=stage,
    )
    assert "operator_id='admin' owner_id='owner'" in caplog.text
    assert "reason='incident' outcome=success" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("operator", ["", "anonymous", "owner", "other-admin"])
async def test_unlisted_operator_cannot_read_or_write(service, operator, caplog):
    with pytest.raises(CronRelayError) as error:
        await disable(service, operator_id=operator)
    assert error.value.error_code == 403
    with pytest.raises(CronRelayError):
        await service.list_tasks(operator_id=operator, bot_id="b1", runtime_stage="online")
    service._bots.get_unique_by_id.assert_not_called()
    service._relay.update_cron.assert_not_awaited()
    service._relay.list_all_crons.assert_not_awaited()
    assert "outcome=error error_code=403" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("bot,code", [
    (None, 404),
    ({"bot_type": "personal", "owner_id": "owner"}, 403),
    ({"bot_type": "service"}, 409),
    ({"bot_type": "service", "owner_id": "anonymous"}, 409),
])
async def test_invalid_bot_never_forwards(service, bot, code):
    service._bots.get_unique_by_id.return_value = bot
    with pytest.raises(CronRelayError) as error:
        await disable(service)
    assert error.value.error_code == code
    service._relay.update_cron.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_bot_fails_closed(service):
    service._bots.get_unique_by_id.side_effect = BotLookupAmbiguousError()
    with pytest.raises(CronRelayError) as error:
        await disable(service)
    assert error.value.error_code == 409
    service._relay.update_cron.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"runtime_stage": "draft"}, {"runtime_stage": "invalid"}, {"bot_id": "all"},
    {"task_id": "../other"}, {"task_id": "x?force=true"}, {"task_id": "x%2Fy"},
    {"task_id": ""}, {"reason": "  "}, {"reason": "x" * 1001},
])
async def test_invalid_input_does_not_mutate(service, overrides):
    with pytest.raises(CronRelayError) as error:
        await disable(service, **overrides)
    assert error.value.error_code == 400
    service._relay.update_cron.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"success": False, "message": "task missing", "error_code": 404},
    {"success": False, "data": {"succeeded": 1, "failed": 1}, "failed_targets": [{"device_uuid": "d2"}]},
    {"success": True, "failed_targets": [{"device_uuid": "d2"}]},
    {},
])
async def test_failed_or_partial_response_never_reports_success(service, response, caplog):
    service._relay.update_cron.return_value = response
    result = await disable(service)
    assert result["success"] is False
    assert result["error_code"] >= 400
    assert result.get("failed_targets") == response.get("failed_targets")
    assert result.get("data") == response.get("data")
    assert "outcome=failed_or_partial" in caplog.text


@pytest.mark.asyncio
async def test_transport_exception_propagates_and_is_audited(service, caplog):
    service._relay.update_cron.side_effect = RuntimeError("unreachable")
    with pytest.raises(RuntimeError, match="unreachable"):
        await disable(service)
    assert "outcome=error error_code=500" in caplog.text


@pytest.mark.asyncio
async def test_list_uses_selected_stage_and_owner_without_writes(service):
    result = await service.list_tasks(operator_id="admin", bot_id="b1", runtime_stage="verify")
    assert result["success"] is True
    service._relay.list_all_crons.assert_awaited_once_with(
        user_id="owner", nick_name="Owner", bot_id="b1", runtime_stage="verify",
    )
    service._relay.update_cron.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_preserves_failed_targets(service):
    service._relay.list_all_crons.return_value = {"success": True, "data": [], "failed_targets": [{"reason": "timeout"}]}
    result = await service.list_tasks(operator_id="admin", bot_id="b1", runtime_stage="online")
    assert result["success"] is False
    assert result["failed_targets"] == [{"reason": "timeout"}]
