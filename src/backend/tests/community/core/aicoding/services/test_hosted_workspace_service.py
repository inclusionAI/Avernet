"""Unit tests for AicodingHostedWorkspaceService.ensure_hosted_workspace.

覆盖：
- 幂等：template_config 已有 dima_space_id → 直接返回不调底层创建
- 创建成功 → 调底层创建 + 持久化 template + 返回 workspace_id
- bot 不存在 → BotNotFoundError
- 非 Coding Bot → BotServiceError
- 底层报错 → 异常透出（不吞）
- template_service 持久化失败 → workspace_id 仍返回（让前端可重试持久化）
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


_UNSET = object()


def _make_service(*, workspace_hosting_service=_UNSET):
    """Build the aicoding hosted-workspace service with mocked deps.

    ``workspace_hosting_service`` 默认给一个 MagicMock；显式传 ``None`` 表示
    "社区未配置" 场景，不会被兜底成 mock。
    """
    from agentclaw.community.core.aicoding.services.hosted_workspace_service import (
        AicodingHostedWorkspaceService,
    )

    if workspace_hosting_service is _UNSET:
        workspace_hosting_service = MagicMock()
    return AicodingHostedWorkspaceService(
        bot_repo=MagicMock(),
        template_service=MagicMock(),
        workspace_hosting_service=workspace_hosting_service,
    )


def _make_app_coding_bot(
    bot_id: str = "bot-001",
    owner_id: str = "owner-1",
    template_type: str = "applicationCoding",
    active_engine: str | None = None,
):
    bot = {
        "id": 1,
        "bot_id": bot_id,
        "bot_name": "TestApp",
        "owner_id": owner_id,
        "template_type": template_type,
    }
    if active_engine is not None:
        bot["active_engine"] = active_engine
    return bot


class TestEnsureHostedWorkspace:
    """AicodingHostedWorkspaceService.ensure_hosted_workspace()"""

    def test_idempotent_when_dima_space_id_already_present(self):
        """已有 dima_space_id → 直接返回，不调 DIMA / 不写 template。"""
        svc = _make_service()
        svc._bot_repo.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = {
            "dima_space_id": "W_EXISTING",
            "other_field": "x",
        }

        result = svc.ensure_hosted_workspace("bot-001", "owner-1")

        assert result == "W_EXISTING"
        svc._workspace_hosting_service.create_workspace_for_bot.assert_not_called()
        svc._template_service.create_or_update_template.assert_not_called()

    def test_creates_workspace_and_persists_template(self):
        """无 dima_space_id ∈ 调 DIMA + create_or_update_template。"""
        svc = _make_service()
        svc._bot_repo.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = {"foo": "bar"}

        def fake_create(staff_id, bot_id, bot_name, template_config, raise_on_failure):
            template_config["dima_space_id"] = "W_NEW"
            return "W_NEW"

        svc._workspace_hosting_service.create_workspace_for_bot.side_effect = fake_create

        result = svc.ensure_hosted_workspace("bot-001", "owner-1")

        assert result == "W_NEW"
        svc._workspace_hosting_service.create_workspace_for_bot.assert_called_once()
        call_kwargs = svc._workspace_hosting_service.create_workspace_for_bot.call_args.kwargs
        assert call_kwargs["staff_id"] == "owner-1"
        assert call_kwargs["bot_id"] == "bot-001"
        assert call_kwargs["bot_name"] == "TestApp"
        assert call_kwargs["raise_on_failure"] is True

        svc._template_service.create_or_update_template.assert_called_once()
        persisted = svc._template_service.create_or_update_template.call_args.kwargs[
            "template_config"
        ]
        assert persisted["dima_space_id"] == "W_NEW"
        assert persisted["foo"] == "bar"

    def test_handles_none_template_config(self):
        """template_config 不存在时按空字典处理，仍能正常创建。"""
        svc = _make_service()
        svc._bot_repo.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = None

        def fake_create(staff_id, bot_id, bot_name, template_config, raise_on_failure):
            template_config["dima_space_id"] = "W_NEW"
            return "W_NEW"

        svc._workspace_hosting_service.create_workspace_for_bot.side_effect = fake_create

        result = svc.ensure_hosted_workspace("bot-001", "owner-1")

        assert result == "W_NEW"
        svc._template_service.create_or_update_template.assert_called_once()

    def test_raises_bot_not_found(self):
        """bot 不存在或非 owner → BotNotFoundError。"""
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotNotFoundError,
        )

        svc = _make_service()
        svc._bot_repo.get_by_id_and_owner.return_value = None

        with pytest.raises(BotNotFoundError):
            svc.ensure_hosted_workspace("bot-404", "owner-1")

        svc._workspace_hosting_service.create_workspace_for_bot.assert_not_called()

    def test_allows_non_application_coding_claude_code_bot(self):
        """template_type 非 applicationCoding 但 active_engine=claude_code → 允许创建。"""
        svc = _make_service()
        svc._bot_repo.get_by_id_and_owner.return_value = _make_app_coding_bot(
            template_type="normalCC",
            active_engine="claude_code",
        )
        svc._template_service.get_template_config.return_value = {
            "bot_template_config": {"capabilities": {"dima_workspace": True}},
            "foo": "bar",
        }

        def fake_create(staff_id, bot_id, bot_name, template_config, raise_on_failure):
            template_config["dima_space_id"] = "W_CC"
            return "W_CC"

        svc._workspace_hosting_service.create_workspace_for_bot.side_effect = fake_create

        result = svc.ensure_hosted_workspace("bot-001", "owner-1")

        assert result == "W_CC"
        svc._workspace_hosting_service.create_workspace_for_bot.assert_called_once()

    def test_allows_non_application_coding_aicoding_bot(self):
        """template_type 非 applicationCoding 但 active_engine=aicoding → 允许创建。"""
        svc = _make_service()
        svc._bot_repo.get_by_id_and_owner.return_value = _make_app_coding_bot(
            template_type="personalCoding",
            active_engine="aicoding",
        )
        svc._template_service.get_template_config.return_value = {
            "bot_template_config": {"capabilities": {"dima_workspace": True}},
        }
        svc._workspace_hosting_service.create_workspace_for_bot.return_value = "W_AI"

        result = svc.ensure_hosted_workspace("bot-001", "owner-1")

        assert result == "W_AI"
        svc._workspace_hosting_service.create_workspace_for_bot.assert_called_once()

    def test_raises_for_non_coding_bot(self):
        """既不是 legacy applicationCoding，也不是 claude_code/aicoding engine → BotServiceError。"""
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotServiceError,
        )

        svc = _make_service()
        svc._bot_repo.get_by_id_and_owner.return_value = _make_app_coding_bot(
            template_type="personal",
            active_engine="openclaw",
        )

        with pytest.raises(BotServiceError) as exc_info:
            svc.ensure_hosted_workspace("bot-001", "owner-1")

        assert "workspace" in str(exc_info.value)
        svc._workspace_hosting_service.create_workspace_for_bot.assert_not_called()

    def test_propagates_dima_error(self):
        """DIMA 报错（如空间名已占用）→ 原异常冒泡到调用方。"""
        svc = _make_service()
        svc._bot_repo.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = {}

        dima_error = Exception(
            "DIMA API error [ARK_RS_530013001]: 空间名称【TestApp_bot-001】已经被占用"
        )
        svc._workspace_hosting_service.create_workspace_for_bot.side_effect = dima_error

        with pytest.raises(Exception) as exc_info:
            svc.ensure_hosted_workspace("bot-001", "owner-1")

        assert "ARK_RS_530013001" in str(exc_info.value)
        assert "已经被占用" in str(exc_info.value)
        svc._template_service.create_or_update_template.assert_not_called()

    def test_returns_workspace_id_even_if_persist_fails(self):
        """workspace 创建成功后持久化失败 → 仍返回 workspace_id（不丢已创建结果）。"""
        svc = _make_service()
        svc._bot_repo.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = {}

        def fake_create(staff_id, bot_id, bot_name, template_config, raise_on_failure):
            template_config["dima_space_id"] = "W_NEW"
            return "W_NEW"

        svc._workspace_hosting_service.create_workspace_for_bot.side_effect = fake_create
        svc._template_service.create_or_update_template.side_effect = Exception("DB down")

        result = svc.ensure_hosted_workspace("bot-001", "owner-1")

        assert result == "W_NEW"

    def test_uses_bot_owner_id_as_staff_id_not_operator(self):
        """staff_id 取自 bot.owner_id，不是请求方 user_id。"""
        svc = _make_service()
        bot = _make_app_coding_bot(owner_id="real-owner")
        svc._bot_repo.get_by_id_and_owner.return_value = bot
        svc._template_service.get_template_config.return_value = {}
        svc._workspace_hosting_service.create_workspace_for_bot.return_value = "W_X"

        svc.ensure_hosted_workspace("bot-001", "collaborator-user")

        call_kwargs = svc._workspace_hosting_service.create_workspace_for_bot.call_args.kwargs
        assert call_kwargs["staff_id"] == "real-owner"


class TestHostedWorkspaceNotConfigured:
    """Community (B8): WorkspaceHostingService unbound → workspace_hosting_service is None.

    applicationCoding 路径必须抛清晰错误，而不是 AttributeError。"""

    def test_ensure_hosted_workspace_raises_when_unconfigured(self):
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotServiceError,
        )

        svc = _make_service(workspace_hosting_service=None)
        svc._bot_repo.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = {}

        with pytest.raises(BotServiceError, match="Workspace-hosting service is not configured"):
            svc.ensure_hosted_workspace("bot-001", "owner-1")
