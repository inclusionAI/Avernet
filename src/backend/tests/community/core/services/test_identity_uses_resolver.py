"""Contract tests — IdentityService factory_store → resolver+dispatcher 改造守护。

Task 4 of ``docs/superpowers/plans/2026-06-15-device-sync-supplier-for-bot-cleanup.md``:
IdentityService 之前把 ``device_fs_dispatcher.for_bot`` 当 callable factory 存进
``self._device_fs_factory``。本契约测试守护:

1. ctor 不接 ``device_fs_factory`` 闭包(无 ``_device_fs_factory`` 属性)
2. ctor 显式注入 ``resolver`` + ``device_fs_dispatcher`` 两个对象
3. 业务方法 ``write_file`` (Arca 分支) 通过
   ``resolver.resolve_for_bot(bot_id, owner_id) → dispatcher.dispatch(ctx)``
   取 device_fs,不再走旧的 ``device_fs_factory(bot_id, owner_id)`` 闭包模式。
"""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.services.identity import IdentityService


def _make_ctx(bot_id: str = "bot-1", user_id: str = "owner-1") -> DeviceContext:
    return DeviceContext(
        provider="arca",
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


def _make_identity(
    *,
    resolver=None,
    dispatcher=None,
    bot_repo=None,
) -> IdentityService:
    return IdentityService(
        path_factory=MagicMock(),
        publish_repo=MagicMock(),
        bot_repo=bot_repo or MagicMock(),
        resolver=resolver or MagicMock(),
        device_fs_dispatcher=dispatcher or MagicMock(),
    )


class TestIdentityServiceCtor:
    """ctor 守护:不再有 ``_device_fs_factory``,改注入 resolver + dispatcher 对象。"""

    def test_identity_uses_resolver_not_factory(self):
        sig = inspect.signature(IdentityService.__init__)
        params = set(sig.parameters.keys())
        assert "resolver" in params, (
            "IdentityService.__init__ 应注入 ``resolver`` (DeviceContextResolver)"
        )
        assert "device_fs_dispatcher" in params, (
            "IdentityService.__init__ 仍应注入 ``device_fs_dispatcher`` (类型本身,非 .for_bot 闭包)"
        )

        identity = _make_identity()
        assert not hasattr(identity, "_device_fs_factory"), (
            "IdentityService 不应再储存 ``_device_fs_factory`` 闭包字段"
        )
        assert hasattr(identity, "_resolver")
        assert hasattr(identity, "_device_fs_dispatcher")


class TestIdentityServiceBusinessMethodUsesResolver:
    """业务方法 ``write_file`` 在 Arca 分支通过 resolver+dispatcher 取 device_fs。"""

    @pytest.mark.asyncio
    async def test_write_file_arca_branch_calls_resolver_and_dispatcher(self):
        ctx = _make_ctx(bot_id="bot-1", user_id="owner-1")
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        dispatcher = MagicMock()
        device_fs = MagicMock()
        device_fs.write_file = AsyncMock()
        dispatcher.dispatch.return_value = device_fs

        identity = _make_identity(resolver=resolver, dispatcher=dispatcher)

        # write_file 的 Arca 分支需要:
        #   arca_utils.get_device_info → ('arca', 'sandbox-x')
        # 才走 dispatcher.dispatch 路径
        with patch(
            "agentclaw.community.core.devices.services.device_info.get_device_info",
            return_value=("arca", "sandbox-x"),
        ):
            await identity.write_file(
                Path("/tmp/AGENTS.md"),
                "content",
                bot_id="bot-1",
                owner_id="owner-1",
            )

        resolver.resolve_for_bot.assert_called_once_with("bot-1", "owner-1")
        dispatcher.dispatch.assert_called_once_with(ctx)
        device_fs.write_file.assert_called_once()
