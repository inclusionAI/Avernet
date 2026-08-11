"""Contract tests — ChannelService factory_store → resolver+dispatcher 改造守护。

Task 4 of ``docs/superpowers/plans/2026-06-15-device-sync-supplier-for-bot-cleanup.md``:
ChannelService 之前把 ``device_fs_dispatcher.for_bot`` 当 callable factory 存进
``self._device_fs_factory``。本契约测试守护:

1. ctor 不接 ``device_fs_factory`` 闭包(无 ``_device_fs_factory`` 属性)
2. ctor 显式注入 ``resolver`` + ``device_fs_dispatcher`` 两个对象
3. 业务方法 (``sync_channel_to_openclaw`` / ``generate_openclaw_configs``)
   通过 ``resolver.resolve_for_bot(bot_id, user_id) → dispatcher.dispatch(ctx)``
   取 device_fs,不再走旧的 ``device_fs_factory(bot_id, user_id)`` 闭包模式。
"""
from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.channel.services.channel_service import ChannelService
from agentclaw.community.core.channel.models import ChannelRecord
from agentclaw.community.core.devices.services.device_context import DeviceContext


def _make_ctx(bot_id: str = "bot-1", user_id: str = "user-1") -> DeviceContext:
    return DeviceContext(
        provider="baas",
        conn_info={
            "bind_id": 42,
            "engine_port": 20003,
            "tenant": "team_claw",
            "url": "http://test",
            "headers": {},
        },
        binding_id=42,
        bot_id=bot_id,
        user_id=user_id,
    )


def _make_channel_record(
    *,
    channel_id: int = 1,
    channel_type: str = "dingding",
    stage: str | None = None,
    status: str = "1",
    config: dict | None = None,
    bind_bot_id: str = "bot-1",
    identity_id: str = "user-1",
) -> ChannelRecord:
    return ChannelRecord(
        id=channel_id,
        type=channel_type,
        description="test",
        identity_id=identity_id,
        bind_bot_id=bind_bot_id,
        config=config or {"client_id": "client-x"},
        status=status,
        deleted=0,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        env="dev",
        stage=stage,
    )


def _make_service(
    *,
    resolver=None,
    dispatcher=None,
    repository=None,
    bot_service=None,
    device_sync_dispatcher=None,
) -> ChannelService:
    """Build a ChannelService with mocked deps.

    Defaults bot_service.get_bot to a non-teclaw bot so the openclaw write
    path (the one that calls dispatcher) runs.
    """
    repo = repository or MagicMock()
    bot_svc = bot_service
    if bot_svc is None:
        bot_svc = MagicMock()
        bot_svc.get_bot.return_value = {"active_engine": "openclaw"}
        bot_svc.get_bot_work_path.return_value = Path("/tmp/bot")
    return ChannelService(
        repository=repo,
        resolver=resolver or MagicMock(),
        device_fs_dispatcher=dispatcher or MagicMock(),
        bot_service=bot_svc,
        device_sync_dispatcher=device_sync_dispatcher or MagicMock(),
    )


class TestChannelServiceCtor:
    """ctor 守护:不再有 ``_device_fs_factory``,改注入 resolver + dispatcher 对象。"""

    def test_channel_service_uses_resolver_not_factory(self):
        sig = inspect.signature(ChannelService.__init__)
        params = set(sig.parameters.keys())
        assert "resolver" in params, (
            "ChannelService.__init__ 应注入 ``resolver`` (DeviceContextResolver)"
        )
        assert "device_fs_dispatcher" in params, (
            "ChannelService.__init__ 仍应注入 ``device_fs_dispatcher`` (类型本身,非 .for_bot 闭包)"
        )

        service = _make_service()
        assert not hasattr(service, "_device_fs_factory"), (
            "ChannelService 不应再储存 ``_device_fs_factory`` 闭包字段"
        )
        assert hasattr(service, "_resolver")
        assert hasattr(service, "_device_fs_dispatcher")


class TestChannelServiceBusinessMethodCallsResolver:
    """业务方法 (``sync_channel_to_openclaw`` / ``generate_openclaw_configs``)
    通过 ``resolver.resolve_for_bot(bot_id, user_id) → dispatcher.dispatch(ctx)``
    取 device_fs。"""

    @pytest.mark.asyncio
    async def test_sync_channel_to_openclaw_calls_resolver_and_dispatcher(self):
        ctx = _make_ctx(bot_id="bot-1", user_id="user-1")
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        dispatcher = MagicMock()
        device_fs = MagicMock()
        dispatcher.dispatch.return_value = device_fs

        repository = MagicMock()
        repository.get_by_id.return_value = _make_channel_record(
            channel_id=1, bind_bot_id="bot-1", identity_id="user-1",
        )

        service = _make_service(
            resolver=resolver, dispatcher=dispatcher, repository=repository,
        )

        json_config = AsyncMock()
        json_config.get.return_value = None
        json_config.exists.return_value = False
        json_config.save = AsyncMock()
        json_class = MagicMock()
        json_class.load = AsyncMock(return_value=json_config)
        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile",
            json_class,
        ):
            await service.sync_channel_to_openclaw(1, action="apply")

        resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
        dispatcher.dispatch.assert_called_once_with(ctx)
        # dispatcher 返回的 device_fs 被传给 JsonConfigFile.load
        json_class.load.assert_called_once()
        _, kwargs_or_args = json_class.load.call_args
        # device_fs is the second positional arg of JsonConfigFile.load(path, device_fs)
        assert json_class.load.call_args.args[1] is device_fs

    @pytest.mark.asyncio
    async def test_generate_openclaw_configs_calls_resolver_and_dispatcher(self):
        ctx = _make_ctx(bot_id="bot-x", user_id="owner-x")
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        dispatcher = MagicMock()
        device_fs = MagicMock()
        dispatcher.dispatch.return_value = device_fs

        repository = MagicMock()
        repository.get_by_type_and_identity_ids.return_value = []

        bot_service = MagicMock()
        bot_service.get_bot_work_path.return_value = Path("/tmp/bot-x")

        service = _make_service(
            resolver=resolver,
            dispatcher=dispatcher,
            repository=repository,
            bot_service=bot_service,
        )

        # NOTE: use MagicMock (not AsyncMock) for json_config — to_dict is sync
        # and copy.deepcopy in _remove_dingtalk_config chokes on AsyncMock's
        # coroutine attrs. Only `load` itself needs to be awaitable.
        json_config = MagicMock()
        json_config.to_dict.return_value = {}
        json_class = MagicMock()
        json_class.load = AsyncMock(return_value=json_config)
        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile",
            json_class,
        ):
            await service.generate_openclaw_configs(
                bot_id="bot-x", owner_id="owner-x",
            )

        resolver.resolve_for_bot.assert_called_once_with("bot-x", "owner-x")
        dispatcher.dispatch.assert_called_once_with(ctx)
        assert json_class.load.call_args.args[1] is device_fs
