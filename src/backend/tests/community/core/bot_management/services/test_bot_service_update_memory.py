"""Tests for update_bot memory re-initialization when memory sources change."""

import pytest
from unittest.mock import Mock, MagicMock, patch

from agentclaw.community.core.bot_management.services.bot_service import BotService

_PATCH_TARGET = "agentclaw.community.core.bot_management.utils.trigger_memory_initialization"


def _make_bot_service(repository=None, template_service=None) -> BotService:
    return BotService(
        drm_reader=MagicMock(),
        repository=repository or Mock(),
        allocation_config=MagicMock(),
        device_binding_repo=MagicMock(),
        skill_set_factory=MagicMock(),
        cleanup_service=MagicMock(),
        bcn_service=MagicMock(),
        bot_publish_repo=MagicMock(),
        passport_plugin=MagicMock(),
        oss_record_repo=MagicMock(),
        bot_publish_service_provider=lambda: MagicMock(),
        device_service_provider=lambda: MagicMock(),
        path_factory=MagicMock(),
        template_service=template_service or MagicMock(),
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(),
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: MagicMock(is_teclaw=MagicMock(return_value=False)),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=lambda: MagicMock(),
    )


def _bot_record(**overrides):
    base = {
        "id": 1,
        "bot_id": "bot-1",
        "bot_name": "TestBot",
        "bot_desc": "desc",
        "owner_id": "user1",
        "status": "ACTIVE",
        "binding_id": None,
        "ext": {},
        "template_type": "applicationCoding",
        "active_engine": "claude_code",
    }
    base.update(overrides)
    return base


class TestUpdateBotMemoryReinit:
    """update_bot 中语雀知识库或代码仓库变化时应重新触发 memory 初始化。"""

    @pytest.fixture
    def repo(self):
        repo = Mock()
        repo.get_by_id_and_owner.return_value = _bot_record()
        repo.update_by_owner.return_value = _bot_record()
        repo.get_by_bot_name.return_value = None
        return repo

    @pytest.fixture
    def template_svc(self):
        svc = MagicMock()
        svc.exists_template.return_value = True
        svc.get_template.return_value = {"ext": {}}
        return svc

    # ── 语雀知识库变化 ──────────────────────────────────────────

    def test_triggers_memory_when_yuque_changes(self, repo, template_svc):
        """语雀知识库从空变为有值时，应调用 trigger_memory_initialization。"""
        template_svc.get_template_config.return_value = {}
        service = _make_bot_service(repository=repo, template_service=template_svc)

        new_config = {"yuque_kb_repos": [{"url": "https://yuque.antfin.com/a/b"}]}

        with patch(_PATCH_TARGET) as mock_trigger:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config=new_config,
                cookie="test-cookie",
            )
            mock_trigger.assert_called_once_with(
                bot_id="bot-1",
                bot_name="TestBot",
                user_id="user1",
                template_config=new_config,
                cookie="test-cookie",
                aixcore_base_url="",
                aixcore_base_url_pre="",
            )

    def test_no_trigger_when_yuque_unchanged(self, repo, template_svc):
        """语雀知识库未发生变化时，不应调用 trigger_memory_initialization。"""
        old_config = {"yuque_kb_repos": [{"url": "https://yuque.antfin.com/a/b"}]}
        template_svc.get_template_config.return_value = old_config
        service = _make_bot_service(repository=repo, template_service=template_svc)

        new_config = {"yuque_kb_repos": [{"url": "https://yuque.antfin.com/a/b"}]}

        with patch(_PATCH_TARGET) as mock_trigger:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config=new_config,
                cookie="test-cookie",
            )
            mock_trigger.assert_not_called()

    # ── 代码仓库变化 ────────────────────────────────────────────

    def test_triggers_memory_when_code_repo_changes(self, repo, template_svc):
        """代码仓库从空变为有值时，应调用 trigger_memory_initialization。"""
        template_svc.get_template_config.return_value = {}
        service = _make_bot_service(repository=repo, template_service=template_svc)

        new_config = {"backend_repo": [{"repo_url": "https://code.teamclaw.com/a/b"}]}

        with patch(_PATCH_TARGET) as mock_trigger:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config=new_config,
                cookie="test-cookie",
            )
            mock_trigger.assert_called_once()

    def test_triggers_memory_when_code_repo_replaced(self, repo, template_svc):
        """代码仓库替换时，应调用 trigger_memory_initialization。"""
        old_config = {"backend_repo": [{"repo_url": "https://code.teamclaw.com/old"}]}
        template_svc.get_template_config.return_value = old_config
        service = _make_bot_service(repository=repo, template_service=template_svc)

        new_config = {"backend_repo": [{"repo_url": "https://code.teamclaw.com/new"}]}

        with patch(_PATCH_TARGET) as mock_trigger:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config=new_config,
                cookie="test-cookie",
            )
            mock_trigger.assert_called_once()

    def test_no_trigger_when_code_repo_unchanged(self, repo, template_svc):
        """代码仓库未变化时，不应调用 trigger_memory_initialization。"""
        config = {"backend_repo": [{"repo_url": "https://code.teamclaw.com/a"}]}
        template_svc.get_template_config.return_value = config
        service = _make_bot_service(repository=repo, template_service=template_svc)

        with patch(_PATCH_TARGET) as mock_trigger:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config=config,
                cookie="test-cookie",
            )
            mock_trigger.assert_not_called()

    def test_no_trigger_when_both_unchanged(self, repo, template_svc):
        """语雀和代码仓库都未变化时，不应调用。"""
        config = {
            "yuque_kb_repos": [{"url": "https://yuque/a"}],
            "backend_repo": [{"repo_url": "https://code/a"}],
        }
        template_svc.get_template_config.return_value = config
        service = _make_bot_service(repository=repo, template_service=template_svc)

        with patch(_PATCH_TARGET) as mock_trigger:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config=config,
                cookie="test-cookie",
            )
            mock_trigger.assert_not_called()

    # ── 条件过滤 ────────────────────────────────────────────────

    def test_no_trigger_when_not_application_coding(self, repo, template_svc):
        """非 applicationCoding 类型的 Bot 不应触发 memory 初始化。"""
        repo.get_by_id_and_owner.return_value = _bot_record(template_type="personalCoding")
        repo.update_by_owner.return_value = _bot_record(template_type="personalCoding")
        template_svc.get_template_config.return_value = {}
        service = _make_bot_service(repository=repo, template_service=template_svc)

        new_config = {"yuque_kb_repos": [{"url": "https://yuque.antfin.com/new"}]}

        with patch(_PATCH_TARGET) as mock_trigger:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config=new_config,
                cookie="test-cookie",
            )
            mock_trigger.assert_not_called()

    def test_no_trigger_when_not_claude_code(self, repo, template_svc):
        """非 claude_code 引擎的 Bot 不应触发 memory 初始化。"""
        repo.get_by_id_and_owner.return_value = _bot_record(active_engine="aicoding")
        repo.update_by_owner.return_value = _bot_record(active_engine="aicoding")
        template_svc.get_template_config.return_value = {}
        service = _make_bot_service(repository=repo, template_service=template_svc)

        new_config = {"backend_repo": [{"repo_url": "https://code.teamclaw.com/new"}]}

        with patch(_PATCH_TARGET) as mock_trigger:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config=new_config,
                cookie="test-cookie",
            )
            mock_trigger.assert_not_called()

    def test_no_trigger_when_no_template_config(self, repo, template_svc):
        """未提交 template_config 时不触发 memory 初始化。"""
        service = _make_bot_service(repository=repo, template_service=template_svc)

        with patch(_PATCH_TARGET) as mock_trigger:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                bot_name="New Name",
                cookie="test-cookie",
            )
            mock_trigger.assert_not_called()

    # ── 容错 ────────────────────────────────────────────────────

    def test_memory_init_failure_does_not_break_update(self, repo, template_svc):
        """memory 初始化失败不应阻断 update_bot 主流程。"""
        template_svc.get_template_config.return_value = {}
        service = _make_bot_service(repository=repo, template_service=template_svc)

        new_config = {"yuque_kb_repos": [{"url": "https://yuque.antfin.com/a/b"}]}

        with patch(
            _PATCH_TARGET,
            side_effect=Exception("network error"),
        ):
            result = service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config=new_config,
                cookie="test-cookie",
            )
            assert result is not None
