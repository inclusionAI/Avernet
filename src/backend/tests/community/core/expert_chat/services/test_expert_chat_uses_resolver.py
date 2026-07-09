"""Tests for ExpertChatService _get_connection 走 resolver — Task 2.5.

改造前: ``_get_connection`` 调用
``self._device_provider.get_device_connection_v2(binding_id, ...)``。

改造后: ``_get_connection`` 调用
``self._resolver.resolve_for_bot(bot_id, user_id)``,
不再依赖 ``binding_id`` 入参(resolver 内部查 binding 表)。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.core.expert_chat.services.expert_chat_service import ExpertChatService


def _make_service(*, resolver, collaborator_service=None, device_provider=None):
    return ExpertChatService(
        repository=MagicMock(),
        bot_repo=MagicMock(),
        device_provider=device_provider or MagicMock(),
        baas_service=MagicMock(),
        resolver=resolver,
        collaborator_service=collaborator_service or MagicMock(),
        transport=MagicMock(invoke=AsyncMock()),
    )


class TestGetConnectionUsesResolver:
    """改造后 _get_connection 走 resolver,不再调 device v2。"""

    def test_get_connection_calls_resolver_not_v2(self):
        """改造后:权限通过后调 resolver.resolve_for_bot,v2 应完全不被调。"""
        # resolver 返回一个 ctx，conn_info 是关键字段集
        ctx = MagicMock()
        ctx.conn_info = {
            "url": "http://resolver-built-url",
            "headers": {"X-Token": "tok"},
            "use_proxy": False,
            "engine_type": "openclaw",
            "type": "local",
        }
        resolver = MagicMock()
        resolver.resolve_for_bot = MagicMock(return_value=ctx)

        # v2 必须不被调到
        device_provider = MagicMock()

        # owner 调自己的 bot — 权限直通,不打到 collaborator
        collab = MagicMock()

        svc = _make_service(
            resolver=resolver,
            collaborator_service=collab,
            device_provider=device_provider,
        )

        bot = {"bot_id": "bot1", "owner_id": "owner1", "public": "0"}
        conn = svc._get_connection(bot, user_id="owner1")

        # 1. resolver 被调一次,入参 (bot_id, user_id) — 与 Task 2.1-2.4 姿势一致
        resolver.resolve_for_bot.assert_called_once_with("bot1", "owner1")

        # 2. v2 完全不被调(资源被 resolver 接管)
        device_provider.get_device_connection_v2.assert_not_called()

        # 3. 输出字段透传自 ctx.conn_info
        assert conn["url"] == "http://resolver-built-url"
        assert conn["headers"] == {"X-Token": "tok"}
        assert conn["engine_type"] == "openclaw"
