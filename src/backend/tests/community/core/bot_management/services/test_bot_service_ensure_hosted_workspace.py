"""Unit tests for BotService.ensure_hosted_workspace.

Covers:
- 幂等：template_config 已有 dima_space_id → 直接返回不调 DIMA
- 创建成功 → 调 DIMA + 持久化 template + 返回 workspace_id
- bot 不存在 → BotNotFoundError
- 非 applicationCoding → BotServiceError
- DIMA 报错 → 异常透出（不吞）
- template_service 持久化失败 → workspace_id 仍返回（让前端可重试持久化）
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_service():
    """Construct BotService with mocked deps, bypassing @inject."""
    from agentclaw.community.core.bot_management.services.bot_service import BotService

    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._template_service = MagicMock()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_config = MagicMock(aixcore_base_url="", aixcore_base_url_pre="")
    return svc


def _make_app_coding_bot(bot_id: str = "bot-001", owner_id: str = "owner-1"):
    return {
        "id": 1,
        "bot_id": bot_id,
        "bot_name": "TestApp",
        "owner_id": owner_id,
        "template_type": "applicationCoding",
    }


class TestEnsureDimaWorkspace:
    """BotService.ensure_hosted_workspace()"""

    def test_idempotent_when_dima_space_id_already_present(self):
        """已有 dima_space_id → 直接返回，不调 DIMA / 不写 template。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotService

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = {
            "dima_space_id": "W_EXISTING",
            "other_field": "x",
        }

        result = BotService.ensure_hosted_workspace(svc, "bot-001", "owner-1")

        assert result == "W_EXISTING"
        svc._workspace_hosting_service.create_workspace_for_bot.assert_not_called()
        svc._template_service.create_or_update_template.assert_not_called()

    def test_creates_workspace_and_persists_template(self):
        """无 dima_space_id → 调 DIMA + create_or_update_template。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotService

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = {"foo": "bar"}

        # DIMA 调用会 inline 修改 template_config 并返回 workspace_id
        def fake_create(staff_id, bot_id, bot_name, template_config, raise_on_failure):
            template_config["dima_space_id"] = "W_NEW"
            return "W_NEW"

        svc._workspace_hosting_service.create_workspace_for_bot.side_effect = fake_create

        result = BotService.ensure_hosted_workspace(svc, "bot-001", "owner-1")

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
        from agentclaw.community.core.bot_management.services.bot_service import BotService

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = None

        def fake_create(staff_id, bot_id, bot_name, template_config, raise_on_failure):
            template_config["dima_space_id"] = "W_NEW"
            return "W_NEW"

        svc._workspace_hosting_service.create_workspace_for_bot.side_effect = fake_create

        result = BotService.ensure_hosted_workspace(svc, "bot-001", "owner-1")

        assert result == "W_NEW"
        svc._template_service.create_or_update_template.assert_called_once()

    def test_raises_bot_not_found(self):
        """bot 不存在或非 owner → BotNotFoundError。"""
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotNotFoundError,
            BotService,
        )

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None

        with pytest.raises(BotNotFoundError):
            BotService.ensure_hosted_workspace(svc, "bot-404", "owner-1")

        svc._workspace_hosting_service.create_workspace_for_bot.assert_not_called()

    def test_raises_for_non_application_coding_bot(self):
        """template_type 不是 applicationCoding → BotServiceError。"""
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotServiceError,
            BotService,
        )

        svc = _make_service()
        bot = _make_app_coding_bot()
        bot["template_type"] = "personal"
        svc._repository.get_by_id_and_owner.return_value = bot

        with pytest.raises(BotServiceError) as exc_info:
            BotService.ensure_hosted_workspace(svc, "bot-001", "owner-1")

        assert "applicationCoding" in str(exc_info.value)
        svc._workspace_hosting_service.create_workspace_for_bot.assert_not_called()

    def test_propagates_dima_error(self):
        """DIMA 报错（如空间名已占用）→ 原异常冒泡到调用方。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotService

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = {}

        dima_error = Exception(
            "DIMA API error [ARK_RS_530013001]: 空间名称【TestApp_bot-001】已经被占用"
        )
        svc._workspace_hosting_service.create_workspace_for_bot.side_effect = dima_error

        with pytest.raises(Exception) as exc_info:
            BotService.ensure_hosted_workspace(svc, "bot-001", "owner-1")

        assert "ARK_RS_530013001" in str(exc_info.value)
        assert "已经被占用" in str(exc_info.value)
        svc._template_service.create_or_update_template.assert_not_called()

    def test_returns_workspace_id_even_if_persist_fails(self):
        """workspace 创建成功后持久化失败 → 仍返回 workspace_id（不丢已创建结果）。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotService

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = {}

        def fake_create(staff_id, bot_id, bot_name, template_config, raise_on_failure):
            template_config["dima_space_id"] = "W_NEW"
            return "W_NEW"

        svc._workspace_hosting_service.create_workspace_for_bot.side_effect = fake_create
        svc._template_service.create_or_update_template.side_effect = Exception("DB down")

        result = BotService.ensure_hosted_workspace(svc, "bot-001", "owner-1")

        assert result == "W_NEW"

    def test_uses_bot_owner_id_as_staff_id_not_operator(self):
        """staff_id 取自 bot.owner_id，不是请求方 user_id。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotService

        svc = _make_service()
        bot = _make_app_coding_bot(owner_id="real-owner")
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._template_service.get_template_config.return_value = {}
        svc._workspace_hosting_service.create_workspace_for_bot.return_value = "W_X"

        BotService.ensure_hosted_workspace(svc, "bot-001", "collaborator-user")

        call_kwargs = svc._workspace_hosting_service.create_workspace_for_bot.call_args.kwargs
        assert call_kwargs["staff_id"] == "real-owner"


class TestDimaNotConfigured:
    """Community (B8): WorkspaceHostingService unbound → _workspace_hosting_service is
    None. The applicationCoding paths must raise a clear error, not AttributeError."""

    def test_ensure_hosted_workspace_raises_when_unconfigured(self):
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotService,
            BotServiceError,
        )

        svc = _make_service()
        svc._workspace_hosting_service = None
        svc._repository.get_by_id_and_owner.return_value = _make_app_coding_bot()
        svc._template_service.get_template_config.return_value = {}

        with pytest.raises(BotServiceError, match="Workspace-hosting service is not configured"):
            BotService.ensure_hosted_workspace(svc, "bot-001", "owner-1")

    def test_require_workspace_hosting_returns_service_when_configured(self):
        from agentclaw.community.core.bot_management.services.bot_service import BotService

        svc = _make_service()
        sentinel = svc._workspace_hosting_service  # the MagicMock set by _make_service
        assert BotService._require_workspace_hosting(svc) is sentinel
