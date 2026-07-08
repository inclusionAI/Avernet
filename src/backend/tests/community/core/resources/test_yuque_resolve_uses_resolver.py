"""Task 2.4 — yuque_resolve._get_engine_url 走 resolver.

改造前签名:
    _get_engine_url(bot_id, user_id, bot_service, device_service)
内部:bot_service.get_bot(...) 拿 binding_id → device_service.get_device_connection_v2(...).

改造后签名(本 task 落):
    _get_engine_url(bot_id, user_id, resolver)
内部:resolver.resolve_for_bot(bot_id, user_id) → DeviceContext.conn_info.

测试覆盖:
    1. resolver 路径替代 v2:不再调 device_service.get_device_connection_v2,
       只调 resolver.resolve_for_bot(bot_id, user_id).
    2. 入参 (bot_id, user_id) 不变(plan 已注明).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.resources.yuque_resolve import _get_engine_url


def _make_ctx(provider="baas", binding_id=42, bot_id="bot-1", user_id="user-1"):
    return DeviceContext(
        provider=provider,
        conn_info={"url": "http://test", "headers": {}, "binding_id": binding_id},
        binding_id=binding_id,
        bot_id=bot_id,
        user_id=user_id,
    )


def test_get_engine_url_calls_resolver_not_v2():
    """改造后:只调 resolver.resolve_for_bot(bot_id, user_id),
    不再调 device_service.get_device_connection_v2.
    """
    resolver = MagicMock()
    ctx = _make_ctx()
    resolver.resolve_for_bot.return_value = ctx

    # device_service 不应被触达 — 旧的 get_device_connection_v2 完全退场
    device_service = MagicMock()

    result = _get_engine_url(
        bot_id="bot-1",
        user_id="user-1",
        resolver=resolver,
    )

    # 1. resolver 被调,入参 (bot_id, user_id)
    resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
    # 2. v2 完全不被触达
    device_service.get_device_connection_v2.assert_not_called()
    # 3. 返回 ctx.conn_info 透传
    assert result == ctx.conn_info
