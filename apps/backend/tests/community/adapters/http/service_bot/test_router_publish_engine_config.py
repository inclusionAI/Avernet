"""服务 Bot 发布单引擎配置查询接口单测。

The endpoint delegates to ``EngineConfigService`` (provider-blind); these tests pin the
router's own logic: 404, engine-type resolution + pass-through, the None→empty mapping,
malformed-JSON → error, and that real failures surface (not swallowed as empty config).
The device-read matrix lives in ``tests/core/services/test_engine_config_service.py``.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.service_bot import router_publish
from agentclaw.community.core.devices.services.device_context import DeviceNotBoundError
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.core.service_bot.services.baas_service import BaasService


_USER = AuthenticatedUser(id="owner-1", staffId="owner-1", operatorName="owner-1")


def _record(
    *,
    publish_id: int = 123,
    status: PublishStatus | str = PublishStatus.VALIDATING,
    binding: dict[str, object] | None = None,
    source_bot_id: str = "bot-1",
    owner_id: str = "owner-1",
) -> BotPublishRecord:
    ext = {}
    if binding is not None:
        ext["binding"] = binding

    return BotPublishRecord(
        id=publish_id,
        source_bot_pk=1,
        source_bot_id=source_bot_id,
        publish_bot_id="bot-1-pub-1",
        name="Service Bot",
        owner_id=owner_id,
        status=str(status),
        version=1,
        permission_owner="owner",
        ext=ext,
    )


def _publish_repo(record: BotPublishRecord | None) -> MagicMock:
    repo = MagicMock()
    repo.get_by_id.return_value = record
    return repo


def _ws_info(target: str = "ARCA_sandbox-123:20003", bot_uuid: str = "bot-uuid-123") -> MagicMock:
    ws = MagicMock()
    ws.target = target
    ws.bot_uuid = bot_uuid
    ws.engine_port = 20003
    ws.baas_base_url = "http://baas.example.com"
    ws.tenant = "tenant-1"
    return ws


@pytest.mark.unit
class TestGetSandboxIdFromPublishRecord:
    def test_returns_sandbox_id_from_online_binding(self):
        """success 状态从 online binding 获取 sandbox_id."""
        record = _record(status=PublishStatus.SUCCESS, binding={"online": 456, "verify": 123})
        baas_service = BaasService.__new__(BaasService)
        baas_service.get_ws_info = MagicMock(return_value=_ws_info(target="ARCA_sandbox-xyz:20003"))

        sandbox_id = baas_service.get_sandbox_id_from_publish_record(
            record=record,
            user_id="user-1",
        )

        assert sandbox_id == "sandbox-xyz"
        # 验证使用了 online binding
        baas_service.get_ws_info.assert_called_once_with(bind_id=456, device_affinity="user-1")

    def test_falls_back_to_verify_binding(self):
        """validating 状态使用 verify binding."""
        record = _record(status=PublishStatus.VALIDATING, binding={"verify": 123})
        baas_service = BaasService.__new__(BaasService)
        baas_service.get_ws_info = MagicMock(return_value=_ws_info(target="ARCA_sandbox-verify:20003"))

        sandbox_id = baas_service.get_sandbox_id_from_publish_record(
            record=record,
            user_id="user-1",
        )

        assert sandbox_id == "sandbox-verify"
        baas_service.get_ws_info.assert_called_once_with(bind_id=123, device_affinity="user-1")

    def test_returns_none_when_binding_missing(self):
        """binding 不存在时返回 None."""
        record = _record(binding=None)
        baas_service = BaasService.__new__(BaasService)

        sandbox_id = baas_service.get_sandbox_id_from_publish_record(
            record=record,
            user_id="user-1",
        )

        assert sandbox_id is None

    def test_returns_none_when_target_not_arca(self):
        """target 不是 Arca 格式时返回 None."""
        record = _record(status=PublishStatus.SUCCESS, binding={"online": 456})
        baas_service = BaasService.__new__(BaasService)
        baas_service.get_ws_info = MagicMock(return_value=_ws_info(target="baas-container:20003"))

        sandbox_id = baas_service.get_sandbox_id_from_publish_record(
            record=record,
            user_id="user-1",
        )

        assert sandbox_id is None

    def test_returns_none_on_exception(self):
        """异常情况返回 None."""
        record = _record(status=PublishStatus.SUCCESS, binding={"online": 456})
        baas_service = BaasService.__new__(BaasService)
        baas_service.get_ws_info = MagicMock(side_effect=Exception("connection error"))

        sandbox_id = baas_service.get_sandbox_id_from_publish_record(
            record=record,
            user_id="user-1",
        )

        assert sandbox_id is None


def _engine_config_service(*, returns=None, raises=None) -> MagicMock:
    svc = MagicMock()
    if raises is not None:
        svc.read_publish_config = AsyncMock(side_effect=raises)
    else:
        svc.read_publish_config = AsyncMock(return_value=returns)
    return svc


def _bot_repo(engine: str = "openclaw") -> MagicMock:
    repo = MagicMock()
    repo.get_by_id_and_owner.return_value = {"active_engine": engine}
    repo.get_by_id.return_value = {"active_engine": engine}
    return repo


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetPublishEngineConfig:
    async def test_returns_parsed_config_on_success(self):
        """委托给 EngineConfigService，返回解析后的配置 + 解析的 engine_type。"""
        record = _record(binding={"online": 456})
        svc = _engine_config_service(returns={"stage": "online", "enabled": True})

        resp = await router_publish.get_publish_engine_config(
            publish_id=123,
            user=_USER,
            publish_repo=_publish_repo(record),
            bot_repo=_bot_repo("openclaw"),
            engine_config_service=svc,
        )

        assert resp.success is True
        assert resp.message == "查询成功"
        assert resp.data == {"stage": "online", "enabled": True}
        svc.read_publish_config.assert_awaited_once_with(record, "openclaw")

    async def test_returns_empty_dict_when_file_missing(self):
        """容器内配置文件缺失/为空时（服务返回 {}）返回成功 + 空配置。"""
        record = _record(binding={"online": 456})
        svc = _engine_config_service(returns={})

        resp = await router_publish.get_publish_engine_config(
            publish_id=123,
            user=_USER,
            publish_repo=_publish_repo(record),
            bot_repo=_bot_repo(),
            engine_config_service=svc,
        )

        assert resp.success is True
        assert resp.data == {}
        assert resp.message == "查询成功"

    async def test_returns_404_when_publish_not_found(self):
        """发布记录不存在时返回 404，且不调用服务。"""
        publish_repo = MagicMock()
        publish_repo.get_by_id.return_value = None
        svc = _engine_config_service(returns={})

        resp = await router_publish.get_publish_engine_config(
            publish_id=404,
            user=_USER,
            publish_repo=publish_repo,
            bot_repo=_bot_repo(),
            engine_config_service=svc,
        )

        assert resp.success is False
        assert resp.error_code == 404
        assert resp.message == "发布记录不存在: 404"
        svc.read_publish_config.assert_not_awaited()

    async def test_returns_500_when_json_invalid(self):
        """配置文件 JSON 格式错误（服务抛 JSONDecodeError）时返回格式错误。"""
        record = _record(binding={"online": 456})
        svc = _engine_config_service(raises=json.JSONDecodeError("bad", "{bad", 0))

        resp = await router_publish.get_publish_engine_config(
            publish_id=123,
            user=_USER,
            publish_repo=_publish_repo(record),
            bot_repo=_bot_repo(),
            engine_config_service=svc,
        )

        assert resp.success is False
        assert resp.error_code == 500
        assert resp.message.startswith("配置文件格式错误:")

    async def test_surfaces_device_error_not_swallowed(self):
        """设备无法解析/读取（真实失败）时上抛为业务错误，而非伪装成空配置。"""
        record = _record(binding={"online": 456})
        svc = _engine_config_service(raises=DeviceNotBoundError("binding 999 not found"))

        resp = await router_publish.get_publish_engine_config(
            publish_id=123,
            user=_USER,
            publish_repo=_publish_repo(record),
            bot_repo=_bot_repo(),
            engine_config_service=svc,
        )

        assert resp.success is False
        assert resp.error_code == 500
        assert resp.data is None
        assert resp.message.startswith("查询失败:")

    async def test_resolves_engine_override_default(self):
        """engine_type 由路由层解析后传入服务（这里来自 bot.active_engine）。"""
        record = _record(binding={"online": 456})
        svc = _engine_config_service(returns={"ok": True})

        await router_publish.get_publish_engine_config(
            publish_id=123,
            user=_USER,
            publish_repo=_publish_repo(record),
            bot_repo=_bot_repo("claude_code"),
            engine_config_service=svc,
        )

        svc.read_publish_config.assert_awaited_once_with(record, "claude_code")