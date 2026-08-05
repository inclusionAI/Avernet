"""Line coverage tests for service bot publish router."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.service_bot import router_publish
from agentclaw.community.adapters.http.service_bot.schemas_publish import (
    CreateFirstPublishRequest,
    PublishFlowRequest,
    UpdateBotTypeForOthersRequest,
    UpdateBotTypeRequest,
    UpdatePublishStatusRequest,
    UpgradeBotTypeForOthersRequest,
    UpgradeBotTypeRequest,
    UpgradePublishRequest,
)
from agentclaw.community.core.bot_collaborator.interceptor import InterceptorContext
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    BotAlreadyServiceTypeError,
    BotNotFoundError,
    BotNotServiceTypeError,
    BotPublishServiceError,
    BotTypeNotSupportedError,
    PublishAlreadyExistsError,
    PublishNotFoundError,
    PublishStatusInvalidError,
)
from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowServiceError


_USER = AuthenticatedUser(id="u1", staffId="u1", operatorName="u1")
_ADMIN = AuthenticatedUser(id="100000", staffId="100000", operatorName="admin")
_ANON = AuthenticatedUser(id="anonymous", staffId="anonymous", operatorName="anonymous")


class DictOnlyResult:
    def __init__(self, status=PublishStatus.SUCCESS, message="ok"):
        self.status = status
        self.message = message

    def dict(self):
        return {"status": str(self.status), "message": self.message}


class ModelDumpResult(DictOnlyResult):
    def model_dump(self):
        return {"status": str(self.status), "message": self.message, "dumped": True}


class Record:
    def __init__(self, *, owner_id="owner", source_bot_id="bot", ext=None):
        self.owner_id = owner_id
        self.source_bot_id = source_bot_id
        self.ext = ext

    def to_dict(self):
        return {"owner_id": self.owner_id, "source_bot_id": self.source_bot_id, "ext": self.ext}


def assert_error(resp, code: int, text: str):
    assert resp.success is False
    assert resp.error_code == code
    assert text in resp.message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_from_publish_id_all_paths():
    assert await router_publish.extract_from_publish_id("", InterceptorContext()) == router_publish.PermissionParams()
    assert await router_publish.extract_from_publish_id("1", InterceptorContext()) == router_publish.PermissionParams()

    injector = MagicMock()
    injector.get.side_effect = RuntimeError("no binding")
    assert await router_publish.extract_from_publish_id("1", InterceptorContext(injector=injector)) == router_publish.PermissionParams()

    service = MagicMock()
    injector = MagicMock()
    injector.get.return_value = service

    assert await router_publish.extract_from_publish_id("bad", InterceptorContext(injector=injector)) == router_publish.PermissionParams()

    service.get_publish_by_id.return_value = None
    assert await router_publish.extract_from_publish_id("12", InterceptorContext(injector=injector)) == router_publish.PermissionParams()

    service.get_publish_by_id.return_value = Record(owner_id="owner-1", source_bot_id="bot-1")
    params = await router_publish.extract_from_publish_id("12", InterceptorContext(injector=injector))
    assert params.bot_id == "bot-1"
    assert params.owner_id == "owner-1"

    service.get_publish_by_id.side_effect = RuntimeError("db")
    assert await router_publish.extract_from_publish_id("12", InterceptorContext(injector=injector)) == router_publish.PermissionParams()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_process_publish_success_and_errors():
    req = PublishFlowRequest(publish_id=1)
    flow = MagicMock()
    flow.process = AsyncMock(return_value=ModelDumpResult(message="processed"))
    resp = await router_publish.process_publish(request=req, user=_USER, flow_service=flow)
    assert resp.success is True
    assert resp.data["dumped"] is True
    flow.process.assert_awaited_once_with(publish_id=1, operator="u1")

    flow.process = AsyncMock(return_value=DictOnlyResult(status=PublishStatus.FAILED, message="failed"))
    resp = await router_publish.process_publish(request=req, user=_USER, flow_service=flow)
    assert resp.success is False
    assert resp.data["message"] == "failed"

    resp = await router_publish.process_publish(request=req, user=_ANON, flow_service=flow)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (PublishStatusInvalidError("bad status"), 400),
        (PublishFlowServiceError("flow bad"), 500),
        (RuntimeError("boom"), 500),
    ]:
        flow.process = AsyncMock(side_effect=exc)
        resp = await router_publish.process_publish(request=req, user=_USER, flow_service=flow)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_bot_stage_binding_info_paths():
    service = MagicMock()
    service.get_bot_stage_binding_info.return_value = {"bot_id": "bot", "binding_id": 1}

    resp = await router_publish.get_bot_stage_binding_info(
        "bot", "owner-1", "online", publish_service=service,
    )
    assert resp.success is True
    assert resp.message == "查询成功"
    assert resp.data == {"bot_id": "bot", "binding_id": 1}
    service.get_bot_stage_binding_info.assert_called_once_with(
        bot_id="bot", owner_id="owner-1", stage="online"
    )

    service.get_bot_stage_binding_info.side_effect = BotNotFoundError("bot missing")
    resp = await router_publish.get_bot_stage_binding_info(
        "bot", "owner-1", "online", publish_service=service,
    )
    assert_error(resp, 404, "bot missing")

    service.get_bot_stage_binding_info.side_effect = BotPublishServiceError("service bad")
    resp = await router_publish.get_bot_stage_binding_info(
        "bot", "owner-1", "online", publish_service=service,
    )
    assert_error(resp, 500, "service bad")

    service.get_bot_stage_binding_info.side_effect = RuntimeError("boom")
    resp = await router_publish.get_bot_stage_binding_info(
        "bot", "owner-1", "online", publish_service=service,
    )
    assert_error(resp, 500, "查询绑定信息失败")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_publish_record_paths():
    service = MagicMock()
    service.get_publish_by_id.return_value = Record()
    resp = await router_publish.get_publish_record(1, user=_USER, publish_service=service)
    assert resp.success is True
    assert resp.message == "查询成功"

    service.get_publish_by_id.return_value = None
    resp = await router_publish.get_publish_record(2, user=_USER, publish_service=service)
    assert_error(resp, 404, "发布记录不存在")

    resp = await router_publish.get_publish_record(2, user=_ANON, publish_service=service)
    assert_error(resp, 400, "无法获取用户信息")

    service.get_publish_by_id.side_effect = RuntimeError("db down")
    resp = await router_publish.get_publish_record(3, user=_USER, publish_service=service)
    assert_error(resp, 500, "查询失败")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_first_publish_success_and_errors():
    req = CreateFirstPublishRequest(bot_id="bot", name="name", description="desc")
    service = MagicMock()
    service.create_first_publish_for_bot.return_value = Record()
    user = AuthenticatedUser(id="u1", staffId="u1", operatorName="op", nickName="nick")
    resp = await router_publish.create_first_publish(request=req, user=user, publish_service=service)
    assert resp.success is True
    service.create_first_publish_for_bot.assert_called_once_with(
        bot_id="bot", owner_id="u1", name="name", permission_owner="owner", description="desc", owner_name="u1"
    )

    resp = await router_publish.create_first_publish(request=req, user=_ANON, publish_service=service)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (BotNotFoundError("bot missing"), 404),
        (BotNotServiceTypeError("not service"), 400),
        (PublishAlreadyExistsError("exists"), 409),
        (BotPublishServiceError("service"), 500),
        (RuntimeError("boom"), 500),
    ]:
        service.create_first_publish_for_bot.side_effect = exc
        resp = await router_publish.create_first_publish(request=req, user=_USER, publish_service=service)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upgrade_publish_success_and_errors():
    req = UpgradePublishRequest(publish_id=1)
    service = MagicMock()
    service.upgrade_publish.return_value = Record()
    resp = await router_publish.upgrade_publish(request=req, user=_USER, publish_service=service)
    assert resp.success is True
    service.upgrade_publish.assert_called_once_with(publish_id=1, owner_id="u1")

    resp = await router_publish.upgrade_publish(request=req, user=_ANON, publish_service=service)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (PublishStatusInvalidError("bad"), 400),
        (BotPublishServiceError("forbidden"), 403),
        (RuntimeError("boom"), 500),
    ]:
        service.upgrade_publish.side_effect = exc
        resp = await router_publish.upgrade_publish(request=req, user=_USER, publish_service=service)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_describe_publish_success_and_errors():
    flow = MagicMock()
    flow.describe_publish.return_value = ModelDumpResult(message="status reported")
    resp = await router_publish.describe_publish(1, user=_USER, flow_service=flow)
    assert resp.success is True
    assert resp.data["dumped"] is True
    flow.describe_publish.assert_called_once_with(publish_id=1)

    flow.describe_publish.return_value = DictOnlyResult(status=PublishStatus.FAILED, message="failed")
    resp = await router_publish.describe_publish(1, user=_USER, flow_service=flow)
    assert resp.success is False

    resp = await router_publish.describe_publish(1, user=_ANON, flow_service=flow)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (PublishStatusInvalidError("bad"), 400),
        (PublishFlowServiceError("flow"), 500),
        (RuntimeError("boom"), 500),
    ]:
        flow.describe_publish.side_effect = exc
        resp = await router_publish.describe_publish(1, user=_USER, flow_service=flow)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_scale_progress_success_and_errors():
    flow = MagicMock()
    flow.sync_scale_progress.return_value = ModelDumpResult(message="scale synced")
    resp = await router_publish.sync_scale_progress(1, user=_USER, flow_service=flow)
    assert resp.success is True
    assert resp.data["dumped"] is True
    flow.sync_scale_progress.assert_called_once_with(publish_id=1)

    flow.sync_scale_progress.return_value = DictOnlyResult(status=PublishStatus.FAILED, message="failed")
    resp = await router_publish.sync_scale_progress(1, user=_USER, flow_service=flow)
    assert resp.success is False

    resp = await router_publish.sync_scale_progress(1, user=_ANON, flow_service=flow)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (PublishStatusInvalidError("bad"), 400),
        (PublishFlowServiceError("flow"), 500),
        (RuntimeError("boom"), 500),
    ]:
        flow.sync_scale_progress.side_effect = exc
        resp = await router_publish.sync_scale_progress(1, user=_USER, flow_service=flow)
        assert resp.error_code == code
        assert resp.success is False
        flow.sync_scale_progress.side_effect = None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_publish_status_success_and_errors():
    req = UpdatePublishStatusRequest(source_status="draft", target_status="built", failed_status="building")
    service = MagicMock()
    service.get_publish_by_id.return_value = Record(ext={"old": "value"})
    service.update_publish_status_with_ext.return_value = Record(ext={"new": "value"})
    resp = await router_publish.update_publish_status(1, req, user=_USER, publish_service=service)
    assert resp.success is True
    service.update_publish_status_with_ext.assert_called_once_with(
        publish_id=1,
        target_status="built",
        ext={"old": "value", "source_status": "building"},
        source_status="draft",
    )

    service.get_publish_by_id.return_value = Record(ext=None)
    service.update_publish_status_with_ext.return_value = Record(ext={})
    resp = await router_publish.update_publish_status(1, req, user=_USER, publish_service=service)
    assert resp.success is True

    resp = await router_publish.update_publish_status(1, req, user=_ANON, publish_service=service)
    assert_error(resp, 400, "无法获取用户信息")

    service.get_publish_by_id.side_effect = PublishNotFoundError("missing")
    resp = await router_publish.update_publish_status(1, req, user=_USER, publish_service=service)
    assert_error(resp, 404, "missing")

    service.get_publish_by_id.side_effect = RuntimeError("boom")
    resp = await router_publish.update_publish_status(1, req, user=_USER, publish_service=service)
    assert_error(resp, 500, "状态更新失败")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_offline_publish_success_and_errors():
    service = MagicMock()
    service.offline_publish = AsyncMock(return_value={"success": True, "message": "offline ok"})
    resp = await router_publish.offline_publish(1, user=_USER, publish_service=service)
    assert resp.success is True
    assert resp.message == "offline ok"
    service.offline_publish.assert_awaited_once_with(publish_id=1)

    service.offline_publish = AsyncMock(return_value={})
    resp = await router_publish.offline_publish(1, user=_USER, publish_service=service)
    assert resp.success is True
    assert resp.message == "下线成功"

    resp = await router_publish.offline_publish(1, user=_ANON, publish_service=service)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (BotPublishServiceError("service"), 400),
        (RuntimeError("boom"), 500),
    ]:
        service.offline_publish = AsyncMock(side_effect=exc)
        resp = await router_publish.offline_publish(1, user=_USER, publish_service=service)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_restart_publish_success_and_errors():
    flow = MagicMock()
    flow.restart_bot.return_value = {"success": True, "message": "restart ok"}
    resp = await router_publish.restart_publish(1, user=_USER, flow_service=flow)
    assert resp.success is True
    assert resp.message == "restart ok"
    flow.restart_bot.assert_called_once_with(publish_id=1, operator="u1")

    flow.restart_bot.return_value = {}
    resp = await router_publish.restart_publish(1, user=_USER, flow_service=flow)
    assert resp.success is False
    assert resp.message == "重启任务已提交"

    resp = await router_publish.restart_publish(1, user=_ANON, flow_service=flow)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (PublishStatusInvalidError("bad"), 400),
        (PublishFlowServiceError("flow"), 500),
        (RuntimeError("boom"), 500),
    ]:
        flow.restart_bot.side_effect = exc
        resp = await router_publish.restart_publish(1, user=_USER, flow_service=flow)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_restart_publish_for_others_success_and_errors():
    publish_service = MagicMock()
    publish_service.get_publish_by_id.return_value = Record(owner_id="owner-9")
    flow = MagicMock()
    flow.restart_bot.return_value = {"success": True, "message": "restart ok"}
    resp = await router_publish.restart_publish_for_others(1, user=_ADMIN, publish_service=publish_service, flow_service=flow)
    assert resp.success is True
    flow.restart_bot.assert_called_once_with(publish_id=1, operator="owner-9")

    resp = await router_publish.restart_publish_for_others(1, user=_ANON, publish_service=publish_service, flow_service=flow)
    assert_error(resp, 400, "无法获取用户信息")

    resp = await router_publish.restart_publish_for_others(1, user=_USER, publish_service=publish_service, flow_service=flow)
    assert_error(resp, 403, "无权限")

    publish_service.get_publish_by_id.return_value = None
    resp = await router_publish.restart_publish_for_others(1, user=_ADMIN, publish_service=publish_service, flow_service=flow)
    assert_error(resp, 404, "发布记录不存在")

    publish_service.get_publish_by_id.return_value = Record(owner_id="owner-9")
    for exc, code in [
        (PublishStatusInvalidError("bad"), 400),
        (PublishFlowServiceError("flow"), 500),
        (RuntimeError("boom"), 500),
    ]:
        flow.restart_bot.side_effect = exc
        resp = await router_publish.restart_publish_for_others(1, user=_ADMIN, publish_service=publish_service, flow_service=flow)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_restart_status_success_and_errors():
    flow = MagicMock()
    flow.sync_restart_progress.return_value = ModelDumpResult(message="status ok")
    resp = await router_publish.restart_status(1, user=_USER, flow_service=flow)
    assert resp.success is True
    flow.sync_restart_progress.assert_called_once_with(publish_id=1)

    flow.sync_restart_progress.return_value = DictOnlyResult(status=PublishStatus.FAILED, message="failed")
    resp = await router_publish.restart_status(1, user=_USER, flow_service=flow)
    assert resp.success is False

    resp = await router_publish.restart_status(1, user=_ANON, flow_service=flow)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (PublishFlowServiceError("flow"), 500),
        (RuntimeError("boom"), 500),
    ]:
        flow.sync_restart_progress.side_effect = exc
        resp = await router_publish.restart_status(1, user=_USER, flow_service=flow)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_publish_success_and_errors():
    flow = MagicMock()
    flow.retry = AsyncMock(return_value=ModelDumpResult(message="retried"))
    resp = await router_publish.retry_publish(1, user=_USER, flow_service=flow)
    assert resp.success is True
    flow.retry.assert_awaited_once_with(publish_id=1, operator="u1")

    flow.retry = AsyncMock(return_value=DictOnlyResult(status=PublishStatus.FAILED, message="failed"))
    resp = await router_publish.retry_publish(1, user=_USER, flow_service=flow)
    assert resp.success is False

    resp = await router_publish.retry_publish(1, user=_ANON, flow_service=flow)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (PublishFlowServiceError("flow"), 400),
        (RuntimeError("boom"), 500),
    ]:
        flow.retry = AsyncMock(side_effect=exc)
        resp = await router_publish.retry_publish(1, user=_USER, flow_service=flow)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_publish_for_others_success_and_errors():
    publish_service = MagicMock()
    publish_service.get_publish_by_id.return_value = Record(owner_id="owner-9")
    flow = MagicMock()
    flow.retry = AsyncMock(return_value=ModelDumpResult(message="retried"))
    resp = await router_publish.retry_publish_for_others(1, user=_ADMIN, publish_service=publish_service, flow_service=flow)
    assert resp.success is True
    flow.retry.assert_awaited_once_with(publish_id=1, operator="owner-9")

    flow.retry = AsyncMock(return_value=DictOnlyResult(status=PublishStatus.FAILED, message="failed"))
    resp = await router_publish.retry_publish_for_others(1, user=_ADMIN, publish_service=publish_service, flow_service=flow)
    assert resp.success is False

    resp = await router_publish.retry_publish_for_others(1, user=_ANON, publish_service=publish_service, flow_service=flow)
    assert_error(resp, 400, "无法获取用户信息")

    resp = await router_publish.retry_publish_for_others(1, user=_USER, publish_service=publish_service, flow_service=flow)
    assert_error(resp, 403, "无权限")

    publish_service.get_publish_by_id.return_value = None
    resp = await router_publish.retry_publish_for_others(1, user=_ADMIN, publish_service=publish_service, flow_service=flow)
    assert_error(resp, 404, "发布记录不存在")

    publish_service.get_publish_by_id.return_value = Record(owner_id="owner-9")
    for exc, code in [
        (PublishFlowServiceError("flow"), 400),
        (RuntimeError("boom"), 500),
    ]:
        flow.retry = AsyncMock(side_effect=exc)
        resp = await router_publish.retry_publish_for_others(1, user=_ADMIN, publish_service=publish_service, flow_service=flow)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upgrade_bot_type_success_and_errors():
    req = UpgradeBotTypeRequest(bot_id="bot")
    service = MagicMock()
    service.upgrade_bot_to_service.return_value = {"bot": {"id": "bot"}, "publish_record": Record()}
    resp = await router_publish.upgrade_bot_type(request=req, user=_USER, publish_service=service)
    assert resp.success is True
    assert resp.data["publish_record"]["owner_id"] == "owner"
    service.upgrade_bot_to_service.assert_called_once_with(bot_id="bot", owner_id="u1")

    service.upgrade_bot_to_service.return_value = {"bot": {"id": "bot"}, "publish_record": None}
    resp = await router_publish.upgrade_bot_type(request=req, user=_USER, publish_service=service)
    assert resp.data["publish_record"] is None

    resp = await router_publish.upgrade_bot_type(request=req, user=_ANON, publish_service=service)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (BotNotFoundError("missing"), 404),
        (BotAlreadyServiceTypeError("already"), 400),
        (BotTypeNotSupportedError("unsupported"), 400),
        (BotPublishServiceError("service"), 500),
        (RuntimeError("boom"), 500),
    ]:
        service.upgrade_bot_to_service.side_effect = exc
        resp = await router_publish.upgrade_bot_type(request=req, user=_USER, publish_service=service)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upgrade_bot_type_for_others_success_and_errors():
    req = UpgradeBotTypeForOthersRequest(bot_id="bot", owner_id="owner-2")
    service = MagicMock()
    service.upgrade_bot_to_service.return_value = {"bot": {"id": "bot"}, "publish_record": Record()}
    resp = await router_publish.upgrade_bot_type_for_others(request=req, user=_ADMIN, publish_service=service)
    assert resp.success is True
    service.upgrade_bot_to_service.assert_called_once_with(bot_id="bot", owner_id="owner-2")

    resp = await router_publish.upgrade_bot_type_for_others(request=req, user=_ANON, publish_service=service)
    assert_error(resp, 400, "无法获取用户信息")

    resp = await router_publish.upgrade_bot_type_for_others(request=req, user=_USER, publish_service=service)
    assert_error(resp, 403, "无权限")

    service.upgrade_bot_to_service.return_value = {"bot": {"id": "bot"}, "publish_record": None}
    resp = await router_publish.upgrade_bot_type_for_others(request=req, user=_ADMIN, publish_service=service)
    assert resp.data["publish_record"] is None

    for exc, code in [
        (BotNotFoundError("missing"), 404),
        (BotAlreadyServiceTypeError("already"), 400),
        (BotTypeNotSupportedError("unsupported"), 400),
        (BotPublishServiceError("service"), 500),
        (RuntimeError("boom"), 500),
    ]:
        service.upgrade_bot_to_service.side_effect = exc
        resp = await router_publish.upgrade_bot_type_for_others(request=req, user=_ADMIN, publish_service=service)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_bot_type_success_and_errors():
    req = UpdateBotTypeRequest(bot_id="bot", bot_type="service")
    service = MagicMock()
    service.update_bot_type.return_value = {"bot": {"id": "bot", "bot_type": "service"}}
    resp = await router_publish.update_bot_type(request=req, user=_USER, publish_service=service)
    assert resp.success is True
    assert resp.data["bot_type"] == "service"
    service.update_bot_type.assert_called_once_with(bot_id="bot", owner_id="u1", bot_type="service")

    resp = await router_publish.update_bot_type(request=req, user=_ANON, publish_service=service)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (BotNotFoundError("missing"), 404),
        (BotPublishServiceError("service"), 400),
        (RuntimeError("boom"), 500),
    ]:
        service.update_bot_type.side_effect = exc
        resp = await router_publish.update_bot_type(request=req, user=_USER, publish_service=service)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_bot_type_for_others_success_and_errors():
    req = UpdateBotTypeForOthersRequest(bot_id="bot", owner_id="owner-2", bot_type="personal")
    service = MagicMock()
    service.update_bot_type.return_value = {"bot": {"id": "bot", "bot_type": "personal"}}
    resp = await router_publish.update_bot_type_for_others(request=req, user=_ADMIN, publish_service=service)
    assert resp.success is True
    service.update_bot_type.assert_called_once_with(bot_id="bot", owner_id="owner-2", bot_type="personal")

    resp = await router_publish.update_bot_type_for_others(request=req, user=_ANON, publish_service=service)
    assert_error(resp, 400, "无法获取用户信息")

    resp = await router_publish.update_bot_type_for_others(request=req, user=_USER, publish_service=service)
    assert_error(resp, 403, "无权限")

    for exc, code in [
        (BotNotFoundError("missing"), 404),
        (BotPublishServiceError("service"), 400),
        (RuntimeError("boom"), 500),
    ]:
        service.update_bot_type.side_effect = exc
        resp = await router_publish.update_bot_type_for_others(request=req, user=_ADMIN, publish_service=service)
        assert resp.error_code == code
        assert resp.success is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_service_bot_success_and_errors():
    service = MagicMock()
    service.delete_service_bot.return_value = True
    resp = await router_publish.delete_service_bot(1, user=_USER, publish_service=service)
    assert resp.success is True
    assert resp.data == {"deleted": True}
    service.delete_service_bot.assert_called_once_with(publish_id=1)

    resp = await router_publish.delete_service_bot(1, user=_ANON, publish_service=service)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (BotPublishServiceError("service"), 400),
        (RuntimeError("boom"), 500),
    ]:
        service.delete_service_bot.side_effect = exc
        resp = await router_publish.delete_service_bot(1, user=_USER, publish_service=service)
        assert resp.error_code == code
        assert resp.success is False


class RollbackRecord:
    """Record with rollback-specific fields for can_rollback tests."""
    def __init__(
        self,
        *,
        id=1,
        owner_id="owner",
        source_bot_id="bot",
        last_pub_id=None,
        version="1.0.0",
        status=PublishStatus.SUCCESS,
        ext=None,
    ):
        self.id = id
        self.owner_id = owner_id
        self.source_bot_id = source_bot_id
        self.last_pub_id = last_pub_id
        self.version = version
        self.status = status
        self.ext = ext or {}

    def to_dict(self):
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "source_bot_id": self.source_bot_id,
            "last_pub_id": self.last_pub_id,
            "version": self.version,
            "status": str(self.status),
            "ext": self.ext,
        }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_can_rollback_publish_success_and_errors():
    """Test can_rollback_publish endpoint - happy and error cases."""
    # Happy case: can rollback
    publish_service = MagicMock()
    publish_repo = MagicMock()
    record = RollbackRecord(id=2, last_pub_id=1, status=PublishStatus.SUCCESS)
    target_record = RollbackRecord(id=1, version="0.9.0", status=PublishStatus.UPGRADED)
    publish_repo.get_by_id.side_effect = [record, target_record]
    publish_repo.get_by_last_pub_id.return_value = None
    publish_service.can_rollback.return_value = (True, None)

    resp = await router_publish.can_rollback_publish(
        2, user=_USER, publish_service=publish_service, publish_repo=publish_repo
    )
    assert resp.success is True
    assert resp.data["can_rollback"] is True
    assert resp.data["target_publish_id"] == 1
    assert resp.data["target_version"] == "0.9.0"
    publish_service.can_rollback.assert_called_once_with(2)

    # Happy case: cannot rollback (no previous version)
    publish_service = MagicMock()
    publish_repo = MagicMock()
    publish_repo.get_by_id.return_value = RollbackRecord(id=1, last_pub_id=None, status=PublishStatus.SUCCESS)
    publish_service.can_rollback.return_value = (False, "无上一版本")

    resp = await router_publish.can_rollback_publish(
        1, user=_USER, publish_service=publish_service, publish_repo=publish_repo
    )
    assert resp.success is True
    assert resp.data["can_rollback"] is False
    assert resp.data["reason"] == "无上一版本"

    # Error case: anonymous user
    resp = await router_publish.can_rollback_publish(
        1, user=_ANON, publish_service=publish_service, publish_repo=publish_repo
    )
    assert_error(resp, 400, "无法获取用户信息")

    # Error case: publish not found (returns 404 but message is default)
    publish_service = MagicMock()
    publish_repo = MagicMock()
    publish_repo.get_by_id.return_value = None
    resp = await router_publish.can_rollback_publish(
        999, user=_USER, publish_service=publish_service, publish_repo=publish_repo
    )
    assert resp.success is False
    assert resp.error_code == 404

    # Error case: unexpected exception
    publish_service = MagicMock()
    publish_repo = MagicMock()
    publish_repo.get_by_id.side_effect = RuntimeError("db error")
    resp = await router_publish.can_rollback_publish(
        1, user=_USER, publish_service=publish_service, publish_repo=publish_repo
    )
    assert_error(resp, 500, "查询失败")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rollback_publish_success_and_errors():
    """Test rollback_publish endpoint - happy and error cases."""
    publish_service = MagicMock()

    # Happy case: rollback succeeds
    publish_service.rollback_publish = AsyncMock(return_value={
        "target_publish_id": 1,
        "rolled_back_publish_id": 2,
        "deploy_status": "validating",
        "deploy_message": "rollback deploy started",
    })

    resp = await router_publish.rollback_publish(
        2, user=_USER, publish_service=publish_service
    )
    assert resp.success is True
    assert resp.data["target_publish_id"] == 1
    assert resp.data["deploy_status"] == "validating"
    publish_service.rollback_publish.assert_awaited_once_with(publish_id=2, operator="u1", reason=None)

    # Happy case: rollback without deploy status
    publish_service.rollback_publish = AsyncMock(return_value={
        "rolled_back_publish_id": 2,
        "deploy_status": "online_pub",
    })
    resp = await router_publish.rollback_publish(
        2, user=_USER, publish_service=publish_service
    )
    assert resp.success is True
    assert resp.data.get("deploy_status") == "online_pub"

    # Error case: anonymous user
    resp = await router_publish.rollback_publish(
        1, user=_ANON, publish_service=publish_service
    )
    assert_error(resp, 400, "无法获取用户信息")

    # Error cases: various exceptions
    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (PublishStatusInvalidError("invalid status"), 400),
        (BotPublishServiceError("service error"), 400),
        (PublishFlowServiceError("flow error"), 500),
        (RuntimeError("boom"), 500),
    ]:
        publish_service.rollback_publish = AsyncMock(side_effect=exc)

        resp = await router_publish.rollback_publish(
            1, user=_USER, publish_service=publish_service
        )
        assert resp.error_code == code
        assert resp.success is False

        # Reset for next iteration
        publish_service.rollback_publish = AsyncMock(return_value={
            "target_publish_id": 1,
            "deploy_status": "online_pub",
        })


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scale_publish_bot_success_and_errors():
    flow = MagicMock()
    # (#197) scale_bot is now async (routes through the operation runner).
    flow.scale_bot = AsyncMock(
        return_value={"success": True, "message": "scale ok", "target_count": 3}
    )
    resp = await router_publish.scale_publish_bot(1, user=_USER, flow_service=flow)
    assert resp.success is True
    assert resp.message == "scale ok"
    flow.scale_bot.assert_called_once_with(publish_id=1, operator="u1")

    flow.scale_bot.return_value = {}
    resp = await router_publish.scale_publish_bot(1, user=_USER, flow_service=flow)
    assert resp.success is False
    assert resp.message == "扩容任务已提交"

    resp = await router_publish.scale_publish_bot(1, user=_ANON, flow_service=flow)
    assert_error(resp, 400, "无法获取用户信息")

    for exc, code in [
        (PublishNotFoundError("missing"), 404),
        (PublishStatusInvalidError("bad"), 400),
        (PublishFlowServiceError("flow"), 500),
        (RuntimeError("boom"), 500),
    ]:
        flow.scale_bot.side_effect = exc
        resp = await router_publish.scale_publish_bot(1, user=_USER, flow_service=flow)
        assert resp.error_code == code
        assert resp.success is False
        flow.scale_bot.side_effect = None


class MockRequest:
    """Mock FastAPI Request with form data."""
    def __init__(self, form_data: dict):
        self._form_data = form_data

    async def form(self):
        return self._form_data


class ApprovalRecord:
    """Record for approval tests with id field."""
    def __init__(self, *, id=1, owner_id="owner_001", source_bot_id="bot_001", ext=None, status=None):
        self.id = id
        self.owner_id = owner_id
        self.source_bot_id = source_bot_id
        self.ext = ext or {}
        self.status = status or PublishStatus.VALIDATING.value


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_approval_callback_success_and_errors():
    """Test publish_approval_callback endpoint - happy and error cases."""

    # Happy case: callback succeeds
    approval_service = MagicMock()
    approval_service.handle_approval_callback = AsyncMock(return_value={"success": True, "message": "ok"})

    request = MockRequest({
        "publish_id": "123",
        "action": "online",
        "applicant": "user_001",
        "globalUniqueId": "puid_456",
        "lastOperate": "AGREE",
    })
    result = await router_publish.publish_approval_callback(request, service=approval_service)

    assert result["success"] is True
    approval_service.handle_approval_callback.assert_awaited_once_with(
        publish_id=123,
        action="online",
        applicant="user_001",
        puid="puid_456",
        last_operate="AGREE",
    )

    # Error case: invalid publish_id
    request = MockRequest({"publish_id": "invalid", "action": "AGREE"})
    result = await router_publish.publish_approval_callback(request, service=approval_service)
    assert result["success"] is False
    assert "Invalid publish_id" in result["message"]

    # Error case: service returns error
    approval_service.handle_approval_callback = AsyncMock(return_value={"success": False, "message": "approval failed"})
    request = MockRequest({
        "publish_id": "123",
        "action": "online",
        "applicant": "user_001",
        "globalUniqueId": "puid_456",
        "lastOperate": "agree",
    })
    result = await router_publish.publish_approval_callback(request, service=approval_service)
    assert result["success"] is False
    assert result["message"] == "approval failed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_and_process_approval_success_and_errors():
    """Test check_and_process_approval endpoint - happy and error cases."""
    from agentclaw.community.adapters.http.service_bot.schemas_publish import CheckApprovalRequest
    from agentclaw.community.api.publish_approval import ApprovalResult

    approval_service = MagicMock()
    publish_service = MagicMock()

    # Happy case: online action with skip
    approval_service.check_and_process_should_approval = AsyncMock(
        return_value=ApprovalResult(
            should_approval=False,
            status="SKIP",
            approval=None,
            message="无需审批",
        )
    )
    publish_service.get_publish_by_id.return_value = ApprovalRecord(status=PublishStatus.VALIDATING.value)

    request = CheckApprovalRequest(action="online")
    resp = await router_publish.check_and_process_approval(
        1, request=request, user=_USER, approval_service=approval_service, publish_service=publish_service
    )

    assert resp.success is True
    assert resp.data["status"] == "SKIP"
    assert resp.data["should_approval"] is False
    approval_service.check_and_process_should_approval.assert_awaited_once()

    # Happy case: offline action with approval needed
    approval_service.check_and_process_offline_approval = AsyncMock(
        return_value=ApprovalResult(
            should_approval=True,
            status="PROCESSING",
            approval={"puid": "puid_123", "approval_url": "https://example.com"},
            message="审批中",
        )
    )
    publish_service.get_publish_by_id.return_value = ApprovalRecord(status=PublishStatus.SUCCESS.value)

    request = CheckApprovalRequest(action="offline")
    resp = await router_publish.check_and_process_approval(
        1, request=request, user=_USER, approval_service=approval_service, publish_service=publish_service
    )

    assert resp.success is True
    assert resp.data["status"] == "PROCESSING"
    assert resp.data["should_approval"] is True
    approval_service.check_and_process_offline_approval.assert_awaited_once()

    # Error case: anonymous user
    request = CheckApprovalRequest(action="online")
    resp = await router_publish.check_and_process_approval(
        1, request=request, user=_ANON, approval_service=approval_service, publish_service=publish_service
    )
    assert_error(resp, 400, "无法获取用户信息")

    # Error case: publish not found
    publish_service.get_publish_by_id.return_value = None
    request = CheckApprovalRequest(action="online")
    resp = await router_publish.check_and_process_approval(
        999, request=request, user=_USER, approval_service=approval_service, publish_service=publish_service
    )
    assert resp.error_code == 404
    assert "发布单不存在" in resp.message

    # Error case: service throws exception
    publish_service.get_publish_by_id.return_value = ApprovalRecord(status=PublishStatus.VALIDATING.value)
    approval_service.check_and_process_should_approval = AsyncMock(side_effect=RuntimeError("boom"))
    request = CheckApprovalRequest(action="online")
    resp = await router_publish.check_and_process_approval(
        1, request=request, user=_USER, approval_service=approval_service, publish_service=publish_service
    )
    assert_error(resp, 500, "检查审批状态失败")




@pytest.mark.unit
@pytest.mark.asyncio
async def test_draft_restore_query_and_execute_endpoints():
    publish_service = MagicMock()
    publish_service.can_restore_draft.return_value = (
        True, "可以恢复草稿", {"source_publish_id": 1, "source_version": 1}
    )

    query = await router_publish.can_restore_draft(
        2, user=_USER, publish_service=publish_service
    )
    assert query.success is True
    assert query.data["can_restore_draft"] is True
    assert query.data["restore_source"]["source_publish_id"] == 1

    publish_service.restore_draft = AsyncMock(return_value={
        "draft_publish_id": 2,
        "source_publish_id": 1,
        "source_version": 1,
        "status": "restoring",
        "task_id": "draft_restore_test",
    })
    restored = await router_publish.restore_draft(
        2, user=_USER, publish_service=publish_service
    )
    assert restored.success is True
    assert restored.message == "草稿恢复已启动"
    assert restored.data["status"] == "restoring"
    publish_service.restore_draft.assert_awaited_once_with(
        publish_id=2, operator=_USER.staffId
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_draft_restore_status_endpoint_paths():
    publish_service = MagicMock()
    publish_service.get_draft_restore_status.return_value = {
        "draft_publish_id": 2,
        "operation_id": 7,
        "task_id": "pub_2_draft_restore_draft_a1",
        "status": "restoring",
        "operation_state": "id_recorded",
        "source_publish_id": 1,
        "source_version": 1,
        "error": None,
    }

    response = await router_publish.get_draft_restore_status(
        2, 7, user=_USER, publish_service=publish_service
    )
    assert response.success is True
    assert response.data["operation_id"] == 7
    assert response.data["status"] == "restoring"
    publish_service.get_draft_restore_status.assert_called_once_with(
        publish_id=2, operation_id=7
    )

    response = await router_publish.get_draft_restore_status(
        2, 7, user=_ANON, publish_service=publish_service
    )
    assert_error(response, 400, "无法获取用户信息")

    publish_service.get_draft_restore_status.side_effect = PublishNotFoundError(
        "草稿恢复操作不存在"
    )
    response = await router_publish.get_draft_restore_status(
        2, 999, user=_USER, publish_service=publish_service
    )
    assert_error(response, 404, "草稿恢复操作不存在")

    publish_service.get_draft_restore_status.side_effect = RuntimeError("boom")
    response = await router_publish.get_draft_restore_status(
        2, 7, user=_USER, publish_service=publish_service
    )
    assert_error(response, 500, "查询草稿恢复状态失败: boom")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_draft_restore_endpoint_error_paths():
    publish_service = MagicMock()

    query = await router_publish.can_restore_draft(
        2, user=_ANON, publish_service=publish_service
    )
    assert_error(query, 400, "无法获取用户信息")

    restored = await router_publish.restore_draft(
        2, user=_ANON, publish_service=publish_service
    )
    assert_error(restored, 400, "无法获取用户信息")

    for exc, code, message in [
        (PublishNotFoundError("missing"), 404, "missing"),
        (PublishStatusInvalidError("bad status"), 400, "bad status"),
        (PublishFlowServiceError("flow failed"), 500, "flow failed"),
        (RuntimeError("unexpected"), 500, "恢复草稿失败: unexpected"),
    ]:
        publish_service.restore_draft = AsyncMock(side_effect=exc)
        restored = await router_publish.restore_draft(
            2, user=_USER, publish_service=publish_service
        )
        assert_error(restored, code, message)
