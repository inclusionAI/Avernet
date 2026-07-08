"""Tests for ExpertChatService 权限上移 — Task 2.5.

改造前: 权限校验藏在 ``device_service.get_device_connection_v2`` 内部
副作用(见 device_service.py:804-839,4 个分支)。

改造后: ``ExpertChatService._check_chat_access`` 显式校验,语义与旧 v2
等价:
- bot owner 本人 → 放行
- 非 owner 但 bot.public='1' → 放行
- 协作者 → 放行
- 否则 → raise ``ChatPermissionError``

Also: 早失败 — 权限不通过时 resolver 不应被调到。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.expert_chat.errors import ChatPermissionError
from agentclaw.community.core.expert_chat.services.expert_chat_service import ExpertChatService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(
    *,
    resolver=None,
    collaborator_service=None,
    repository=None,
    bot_repo=None,
    device_provider=None,
    baas_service=None,
    transport=None,
):
    """Construct ExpertChatService with mocked deps (含 Task 2.5 新增依赖)."""
    return ExpertChatService(
        repository=repository or MagicMock(),
        bot_repo=bot_repo or MagicMock(),
        device_provider=device_provider or MagicMock(),
        baas_service=baas_service or MagicMock(),
        resolver=resolver or MagicMock(),
        collaborator_service=collaborator_service or MagicMock(),
        transport=transport or MagicMock(invoke=AsyncMock()),
    )


def _resolver_returning_ctx(conn_info: dict | None = None):
    """Construct a resolver mock whose resolve_for_bot returns a ctx
    with .conn_info == conn_info dict."""
    ctx = MagicMock()
    ctx.conn_info = conn_info or {
        "url": "http://localhost:8080",
        "headers": {},
        "use_proxy": False,
        "engine_type": "openclaw",
        "type": "local",
    }
    resolver = MagicMock()
    resolver.resolve_for_bot = MagicMock(return_value=ctx)
    return resolver


def _collab_service_returning(has_permission: bool):
    """Construct a collaborator service mock whose check_collaborator_permission
    returns has_permission flag."""
    svc = MagicMock()
    svc.check_collaborator_permission = MagicMock(
        return_value={"has_permission": has_permission, "level": "MEMBER", "level_value": 1}
    )
    return svc


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestExpertChatPermissionCheck:
    """覆盖 _check_chat_access 4 个分支 + 早失败语义。

    与旧 device_service.get_device_connection_v2 内部 4 分支(owner /
    public / collaborator / reject)等价。
    """

    def test_owner_can_chat(self):
        """分支 1: bot owner 本人调用 → 放行,resolver 被调到。"""
        resolver = _resolver_returning_ctx()
        collab = _collab_service_returning(False)  # collab 不会被问到
        svc = _make_service(resolver=resolver, collaborator_service=collab)

        bot = {"bot_id": "bot1", "owner_id": "owner1", "public": "0"}
        conn = svc._get_connection(bot, user_id="owner1")  # caller == owner

        assert conn["url"] == "http://localhost:8080"
        resolver.resolve_for_bot.assert_called_once_with("bot1", "owner1")
        # owner 路径下不应问 collaborator
        collab.check_collaborator_permission.assert_not_called()

    def test_non_owner_with_public_bot_can_chat(self):
        """分支 2: 非 owner 但 bot.public='1' → 放行 (关键场景:广场公开 bot)。

        旧 device_service.py:811 行 ``is_public = bot.get("public") == "1"``
        放行,等价。
        """
        resolver = _resolver_returning_ctx()
        collab = _collab_service_returning(False)
        svc = _make_service(resolver=resolver, collaborator_service=collab)

        bot = {"bot_id": "bot1", "owner_id": "owner1", "public": "1"}
        conn = svc._get_connection(bot, user_id="visitor1")  # caller != owner

        assert conn["url"] == "http://localhost:8080"
        # P0 修复:resolver 第二参用 owner_id(查 binding 用),而不是 visitor1(caller)。
        # binding 在 owner 名下,用 visitor1 查会查不到 → 误报「Bot 未绑定设备」。
        resolver.resolve_for_bot.assert_called_once_with("bot1", "owner1")
        # public 短路 — 不应问 collaborator
        collab.check_collaborator_permission.assert_not_called()

    def test_collaborator_can_chat(self):
        """分支 3: 非 owner、非 public,但是协作者 → 放行。"""
        resolver = _resolver_returning_ctx()
        collab = _collab_service_returning(True)
        svc = _make_service(resolver=resolver, collaborator_service=collab)

        bot = {"bot_id": "bot1", "owner_id": "owner1", "public": "0"}
        conn = svc._get_connection(bot, user_id="collab1")

        assert conn["url"] == "http://localhost:8080"
        # P0 修复:resolver 用 owner_id 查 binding(binding 在 owner 名下,
        # collab1 查不到);caller 身份只在权限校验和 builder device_affinity 用。
        resolver.resolve_for_bot.assert_called_once_with("bot1", "owner1")
        collab.check_collaborator_permission.assert_called_once()
        # 校验入参与旧 device_service.py:831-836 等价
        call_kwargs = collab.check_collaborator_permission.call_args.kwargs
        assert call_kwargs["bot_id"] == "bot1"
        assert call_kwargs["owner_id"] == "owner1"
        assert call_kwargs["user_id"] == "collab1"

    def test_non_owner_non_collaborator_private_bot_raises_chat_permission_error(self):
        """分支 4: 非 owner、非 public、非 collab → raise ChatPermissionError。

        与旧 device_service.py:838-839 raise
        ``InvalidDeviceStatusError("非公开Bot只能...")`` 等价,
        本期改造抬升为业务异常 ``ChatPermissionError``。
        """
        resolver = _resolver_returning_ctx()
        collab = _collab_service_returning(False)
        svc = _make_service(resolver=resolver, collaborator_service=collab)

        bot = {"bot_id": "bot1", "owner_id": "owner1", "public": "0"}

        with pytest.raises(ChatPermissionError, match="stranger1"):
            svc._get_connection(bot, user_id="stranger1")

    def test_permission_check_happens_before_resolver_called(self):
        """早失败语义 — 权限校验在 resolver 之前。

        权限失败时 resolver 完全不应被调,避免无意义的底层(BaaS /http-info
        等)调用。与旧 v2 实现的行为等价(旧 v2 在 get_device_connection
        内部 raise,不会走到 _compose_device_conn_info)。
        """
        resolver = _resolver_returning_ctx()
        collab = _collab_service_returning(False)
        svc = _make_service(resolver=resolver, collaborator_service=collab)

        bot = {"bot_id": "bot1", "owner_id": "owner1", "public": "0"}

        with pytest.raises(ChatPermissionError):
            svc._get_connection(bot, user_id="stranger1")

        # 关键守护: resolver 不应被调
        resolver.resolve_for_bot.assert_not_called()
