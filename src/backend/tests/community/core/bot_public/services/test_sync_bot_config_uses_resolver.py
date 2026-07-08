"""Task 2.1 — sync_bot_config_to_device 走 resolver + dispatcher.

改造前签名:
    sync_bot_config_to_device(bot_id, binding_id, public, permission_owner,
                              user_id, nick_name)
内部:走 ``_device_sync_supplier.for_binding(binding_id, user_id, nick_name)``。

改造后签名(本 task 落):
    sync_bot_config_to_device(bot_id, user_id, public, permission_owner)
内部:走 ``_resolver.resolve_for_bot(bot_id, user_id)`` → DeviceContext →
``_device_sync_dispatcher.dispatch(ctx)`` → ``plugin.sync_bot_config(...)``。

测试覆盖:
    1. resolver 路径替代 v2:不再调 device_service.get_device_connection_v2,
       只调 resolver.resolve_for_bot(bot_id, user_id)。
    2. baas bot 走 BaasDeviceSyncPlugin.sync_bot_config(经 dispatcher dispatch)。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_public.services.bot_public_service import BotPublicService
from agentclaw.community.core.devices.services.device_context import DeviceContext


def _make_service(
    *,
    resolver=None,
    device_sync_dispatcher=None,
):
    """构造 BotPublicService — 注入 resolver + dispatcher。

    Task 6 收口后 ``device_sync_supplier`` 入参已删除。
    """
    return BotPublicService(
        bot_friend_repo=MagicMock(),
        bot_repository=MagicMock(),
        process_service=MagicMock(),
        bot_service=MagicMock(),
        passport_plugin=MagicMock(),
        auth_relationship_plugin=MagicMock(),
        publish_approval_plugin=MagicMock(),
        skill_set_service_factory=MagicMock(),
        device_context_resolver=resolver or MagicMock(),
        device_sync_dispatcher=device_sync_dispatcher or MagicMock(),
    )


def _make_ctx(provider="baas", binding_id=42, bot_id="bot-1", user_id="user-1"):
    return DeviceContext(
        provider=provider,
        conn_info={"url": "http://test", "binding_id": binding_id},
        binding_id=binding_id,
        bot_id=bot_id,
        user_id=user_id,
    )


class TestSyncBotConfigUsesResolver:
    def test_sync_bot_config_calls_resolver_not_v2(self):
        """改造后:只调 resolver.resolve_for_bot(bot_id, user_id),
        不再调 device_service.get_device_connection_v2,
        也不再走 device_sync_supplier.for_binding。
        """
        resolver = MagicMock()
        ctx = _make_ctx()
        resolver.resolve_for_bot.return_value = ctx

        plugin = MagicMock()
        plugin.sync_bot_config.return_value = {"success": True}
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = plugin

        svc = _make_service(
            resolver=resolver,
            device_sync_dispatcher=dispatcher,
        )
        result = svc.sync_bot_config_to_device(
            bot_id="bot-1",
            user_id="user-1",
            public="1",
            permission_owner="OWNER",
        )

        # 1. resolver 被调,入参是 (bot_id, user_id)
        resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
        # 2. dispatcher 被调,入参是 resolver 的出口 ctx
        dispatcher.dispatch.assert_called_once_with(ctx)
        # 3. 调用结果透传
        assert result == {"success": True}

    def test_sync_bot_config_baas_bot_calls_baas_plugin_sync_bot_config(self):
        """baas provider → dispatcher 派发的 plugin.sync_bot_config 被调,
        binding_id 来自 ctx,user_id 透传,nick_name 死参兜底 = user_id。
        """
        resolver = MagicMock()
        ctx = _make_ctx(provider="baas", binding_id=42)
        resolver.resolve_for_bot.return_value = ctx

        baas_plugin = MagicMock()
        baas_plugin.sync_bot_config.return_value = {"success": True, "via": "baas"}
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = baas_plugin

        svc = _make_service(
            resolver=resolver, device_sync_dispatcher=dispatcher,
        )
        result = svc.sync_bot_config_to_device(
            bot_id="bot-1",
            user_id="user-1",
            public="1",
            permission_owner="OWNER",
        )

        baas_plugin.sync_bot_config.assert_called_once_with(
            bot_id="bot-1",
            binding_id=42,
            public="1",
            permission_owner="OWNER",
            user_id="user-1",
            nick_name="user-1",  # 死参兜底,plugin 内未读
        )
        assert result == {"success": True, "via": "baas"}
