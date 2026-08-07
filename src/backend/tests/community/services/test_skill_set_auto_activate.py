"""Unit tests for skill_set auto-activate with correct device routing.

针对 add_skills_to_set / switch_to_skill_set / sync_skill_set_to_active 中
activate_skill 调用传递 user_id 和 bolt_id 参数的测试。

These construct ``SkillSetService`` directly and never issue an HTTP request,
so they belong here alongside the other ``SkillSetService`` unit tests rather
than under ``tests/community/endpoints/``, where every file is expected to
exercise a real route end to end (see
``tests/community/framework/test_no_mock_in_endpoint_tests.py``).
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio


class TestSkillSetAutoActivateDeviceRouting:
    """测试自动激活时正确传递 user_id 和 bolt_id 参数"""

    @pytest.mark.asyncio
    async def test_add_skills_to_set_auto_activate_with_correct_params(self):
        """测试 add_skills_to_set 自动激活时传递正确的 user_id 和 bolt_id"""
        import sys
        sys.path.insert(0, 'src')
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        # 直接导入并完全 Mock
        skill_repo = MagicMock()
        skill_set_repo = MagicMock()
        mcp_center = MagicMock()
        mcp_config_service = MagicMock()
        skill_service = MagicMock()
        bot_repo = MagicMock()
        device_plugin = MagicMock()
        path_factory = MagicMock()

        # Mock get_by_id 返回带 owner_id 的 bot
        def mock_get_bot(bot_id):
            bot = MagicMock()
            bot.get.return_value = "staff_197444"
            return bot
        bot_repo.get_by_id = mock_get_bot

        # 路径
        test_path = Path("/tmp/test_passes")
        test_path.mkdir(parents=True, exist_ok=True)
        path_factory.get_bot_skills_dir.return_value = test_path
        path_factory.get_bot_skills_repo_dir.return_value = Path("/tmp/test_repo_p")
        path_factory.get_bot_skills_local_dir.return_value = Path("/tmp/test_local_p")

        # Mock skill_set - 必须 is_active=True 才会触发自动激活
        skill_set_repo.get_by_id.return_value = {
            "id": "1",
            "name": "Test Set",
            "is_active": True,  # 关键：激活状态
            "is_default": False,
            "user_id": "197444",
            "bot_id": "bot_123"
        }

        # 关键：skill 必须有 git_path 才会被加入 git_paths 列表
        skill_with_git = MagicMock()
        skill_with_git.get.side_effect = lambda k, default=None: {
            "id": "1",
            "name": "test_skill",
            "git_path": "git://business/test_skill",
            "skill_set_id": "1"
        }.get(k, default)

        skill_repo.get_by_id.return_value = skill_with_git
        skill_repo.list_skills.return_value = [{"id": "1", "name": "test_skill", "git_path": "git://business/test_skill"}]
        skill_set_repo.get_skills_in_set.return_value = []

        # 创建服务
        service = SkillSetService(
            skill_repo=skill_repo,
            skill_set_repo=skill_set_repo,
            mcp_center=mcp_center,
            mcp_config_service=mcp_config_service,
            skill_service=skill_service,
            bot_repo=bot_repo,
            entity_id="staff_197444",
            bot_id="bot_123",
            engine_type="openclaw",
            device_plugin=device_plugin,
            path_factory=path_factory,
        )

        # 直接替换 skill_service.activate_skill
        calls = []
        async def capture_activate(skill_path, user_id=None, bolt_id=None):
            calls.append({"path": skill_path, "user_id": user_id, "bolt_id": bolt_id})
            return True

        service.skill_service.activate_skill = capture_activate
        service.mcp_center.get_mcp_by_skill_set.return_value = []
        service._sync_symlinks_to_device_if_needed = MagicMock(return_value=True)

        # 执行添加 skill 到已激活的技能集
        result = await service.add_skills_to_set(
            skill_set_id="1",
            skill_ids=["1"],
            user_id="197444"
        )

        # 验证 activate_skill 被调用了，并且参数正确
        assert len(calls) > 0, "activate_skill should be called when skill_set is active"

        call = calls[0]
        assert call["path"] == "git://business/test_skill"
        assert call["user_id"] == "staff_197444"
        assert call["bolt_id"] == "bot_123"

        # 清理
        import shutil
        shutil.rmtree("/tmp/test_passes", ignore_errors=True)
        shutil.rmtree("/tmp/test_repo_p", ignore_errors=True)
        shutil.rmtree("/tmp/test_local_p", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_auto_activate_uses_owner_id_for_collaboration_bot(self):
        """测试协作模式（服务Bot）使用 owner_id 而不是 entity_id"""
        import sys
        sys.path.insert(0, 'src')
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        skill_repo = MagicMock()
        skill_set_repo = MagicMock()
        mcp_center = MagicMock()
        mcp_config_service = MagicMock()
        skill_service = MagicMock()
        bot_repo = MagicMock()
        device_plugin = MagicMock()
        path_factory = MagicMock()

        # 协作模式：entity_id 是 team_xxx，但 owner_id 应该是管理员的 staff 号
        def mock_get_bot_collab(bot_id):
            bot = MagicMock()
            # 返回 owner_id（服务Bot 的 owner）
            bot.get.side_effect = lambda k, d=None: {"owner_id": "staff_999999"}.get(k, d)
            return bot
        bot_repo.get_by_id = mock_get_bot_collab

        test_path = Path("/tmp/test_collab")
        test_path.mkdir(parents=True, exist_ok=True)
        path_factory.get_bot_skills_dir.return_value = test_path
        path_factory.get_bot_skills_repo_dir.return_value = Path("/tmp/test_repo_c")
        path_factory.get_bot_skills_local_dir.return_value = Path("/tmp/test_local_c")

        skill_set_repo.get_by_id.return_value = {
            "id": "1",
            "name": "Test Set",
            "is_active": True,
            "is_default": False,
            "user_id": "197444",
            "bot_id": "service_bot_123"
        }

        skill_with_git = MagicMock()
        skill_with_git.get.side_effect = lambda k, default=None: {
            "id": "1",
            "name": "test_skill",
            "git_path": "git://business/test_skill",
            "skill_set_id": "1"
        }.get(k, default)

        skill_repo.get_by_id.return_value = skill_with_git
        skill_repo.list_skills.return_value = [{"id": "1", "name": "test_skill", "git_path": "git://business/test_skill"}]
        skill_set_repo.get_skills_in_set.return_value = []

        # entity_id 是 team_xxx（协作模式）
        service = SkillSetService(
            skill_repo=skill_repo,
            skill_set_repo=skill_set_repo,
            mcp_center=mcp_center,
            mcp_config_service=mcp_config_service,
            skill_service=skill_service,
            bot_repo=bot_repo,
            entity_id="team_xxx",  # 协作模式
            bot_id="service_bot_123",
            engine_type="openclaw",
            device_plugin=device_plugin,
            path_factory=path_factory,
        )

        calls = []
        async def capture_activate(skill_path, user_id=None, bolt_id=None):
            calls.append({"user_id": user_id, "bolt_id": bolt_id})
            return True

        service.skill_service.activate_skill = capture_activate
        service.mcp_center.get_mcp_by_skill_set.return_value = []
        service._sync_symlinks_to_device_if_needed = MagicMock(return_value=True)

        result = await service.add_skills_to_set(
            skill_set_id="1",
            skill_ids=["1"],
            user_id="197444"
        )

        # 验证使用的是 owner_id (staff_999999)，不是 entity_id (team_xxx)
        assert len(calls) > 0
        assert calls[0]["user_id"] == "staff_999999", f"Should use owner_id staff_999999, got {calls[0]['user_id']}"

        import shutil
        shutil.rmtree("/tmp/test_collab", ignore_errors=True)
        shutil.rmtree("/tmp/test_repo_c", ignore_errors=True)
        shutil.rmtree("/tmp/test_local_c", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_fallback_to_entity_id_when_no_bot(self):
        """测试查不到 bot 时 fallback 到 entity_id"""
        import sys
        sys.path.insert(0, 'src')
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        skill_repo = MagicMock()
        skill_set_repo = MagicMock()
        mcp_center = MagicMock()
        mcp_config_service = MagicMock()
        skill_service = MagicMock()
        bot_repo = MagicMock()
        device_plugin = MagicMock()
        path_factory = MagicMock()

        # get_by_id 返回 None（查不到 bot）
        bot_repo.get_by_id.return_value = None

        test_path = Path("/tmp/test_fallback")
        test_path.mkdir(parents=True, exist_ok=True)
        path_factory.get_bot_skills_dir.return_value = test_path
        path_factory.get_bot_skills_repo_dir.return_value = Path("/tmp/test_repo_f")
        path_factory.get_bot_skills_local_dir.return_value = Path("/tmp/test_local_f")

        skill_set_repo.get_by_id.return_value = {
            "id": "1",
            "name": "Test Set",
            "is_active": True,
            "is_default": False,
            "user_id": "197444",
            "bot_id": "bot_123"
        }

        skill_with_git = MagicMock()
        skill_with_git.get.side_effect = lambda k, default=None: {
            "id": "1",
            "name": "test_skill",
            "git_path": "git://business/test_skill",
            "skill_set_id": "1"
        }.get(k, default)

        skill_repo.get_by_id.return_value = skill_with_git
        skill_repo.list_skills.return_value = [{"id": "1", "name": "test_skill", "git_path": "git://business/test_skill"}]
        skill_set_repo.get_skills_in_set.return_value = []

        # 有 entity_id
        service = SkillSetService(
            skill_repo=skill_repo,
            skill_set_repo=skill_set_repo,
            mcp_center=mcp_center,
            mcp_config_service=mcp_config_service,
            skill_service=skill_service,
            bot_repo=bot_repo,
            entity_id="staff_197444",  # 有 entity_id
            bot_id="bot_123",
            engine_type="openclaw",
            device_plugin=device_plugin,
            path_factory=path_factory,
        )

        calls = []
        async def capture_activate(skill_path, user_id=None, bolt_id=None):
            calls.append({"user_id": user_id})
            return True

        service.skill_service.activate_skill = capture_activate
        service.mcp_center.get_mcp_by_skill_set.return_value = []
        service._sync_symlinks_to_device_if_needed = MagicMock(return_value=True)

        result = await service.add_skills_to_set(
            skill_set_id="1",
            skill_ids=["1"],
            user_id="197444"
        )

        # 验证 fallback 到 entity_id
        assert len(calls) > 0
        assert calls[0]["user_id"] == "staff_197444"

        import shutil
        shutil.rmtree("/tmp/test_fallback", ignore_errors=True)
        shutil.rmtree("/tmp/test_repo_f", ignore_errors=True)
        shutil.rmtree("/tmp/test_local_f", ignore_errors=True)


class TestErrorHandling:
    """测试异常处理分支覆盖"""

    @pytest.mark.asyncio
    async def test_skill_set_not_found_raises_error(self):
        """测试 skill_set 不存在时抛出异常"""
        import sys
        sys.path.insert(0, 'src')
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        mocks = {
            "skill_repo": MagicMock(),
            "skill_set_repo": MagicMock(),
            "mcp_center": MagicMock(),
            "mcp_config_service": MagicMock(),
            "skill_service": MagicMock(),
            "bot_repo": MagicMock(),
            "device_plugin": MagicMock(),
            "path_factory": MagicMock(),
        }

        mocks["skill_set_repo"].get_by_id.return_value = None

        test_path = Path("/tmp/test_error")
        test_path.mkdir(parents=True, exist_ok=True)
        mocks["path_factory"].get_bot_skills_dir.return_value = test_path
        mocks["path_factory"].get_bot_skills_repo_dir.return_value = Path("/tmp/test_repo_err")
        mocks["path_factory"].get_bot_skills_local_dir.return_value = Path("/tmp/test_local_err")

        service = SkillSetService(
            skill_repo=mocks["skill_repo"],
            skill_set_repo=mocks["skill_set_repo"],
            mcp_center=mocks["mcp_center"],
            mcp_config_service=mocks["mcp_config_service"],
            skill_service=mocks["skill_service"],
            bot_repo=mocks["bot_repo"],
            entity_id="staff_197444",
            bot_id="bot_123",
            engine_type="openclaw",
            device_plugin=mocks["device_plugin"],
            path_factory=mocks["path_factory"],
        )

        with pytest.raises(ValueError, match="not found"):
            await service.add_skills_to_set(
                skill_set_id="nonexistent",
                skill_ids=["1"],
                user_id="197444"
            )

        import shutil
        shutil.rmtree("/tmp/test_error", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_default_skill_set_cannot_modify(self):
        """测试默认技能集不能修改"""
        import sys
        sys.path.insert(0, 'src')
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        mocks = {
            "skill_repo": MagicMock(),
            "skill_set_repo": MagicMock(),
            "mcp_center": MagicMock(),
            "mcp_config_service": MagicMock(),
            "skill_service": MagicMock(),
            "bot_repo": MagicMock(),
            "device_plugin": MagicMock(),
            "path_factory": MagicMock(),
        }

        mocks["skill_set_repo"].get_by_id.return_value = {
            "id": "1",
            "name": "Default Set",
            "is_active": False,
            "is_default": True,
            "user_id": "197444",
            "bot_id": "bot_123"
        }

        test_path = Path("/tmp/test_default")
        test_path.mkdir(parents=True, exist_ok=True)
        mocks["path_factory"].get_bot_skills_dir.return_value = test_path
        mocks["path_factory"].get_bot_skills_repo_dir.return_value = Path("/tmp/test_repo_def")
        mocks["path_factory"].get_bot_skills_local_dir.return_value = Path("/tmp/test_local_def")

        service = SkillSetService(
            skill_repo=mocks["skill_repo"],
            skill_set_repo=mocks["skill_set_repo"],
            mcp_center=mocks["mcp_center"],
            mcp_config_service=mocks["mcp_config_service"],
            skill_service=mocks["skill_service"],
            bot_repo=mocks["bot_repo"],
            entity_id="staff_197444",
            bot_id="bot_123",
            engine_type="openclaw",
            device_plugin=mocks["device_plugin"],
            path_factory=mocks["path_factory"],
        )

        with pytest.raises(ValueError, match="默认技能集不允许修改"):
            await service.add_skills_to_set(
                skill_set_id="1",
                skill_ids=["1"],
                user_id="197444"
            )

        import shutil
        shutil.rmtree("/tmp/test_default", ignore_errors=True)


class TestActivationFailedVisibility:
    """测试激活失败对调用方可见"""

    @pytest.mark.asyncio
    async def test_activation_failure_included_in_result(self):
        """测试激活失败时，failure 信息写入返回结果"""
        import sys
        sys.path.insert(0, 'src')
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        mocks = {
            "skill_repo": MagicMock(),
            "skill_set_repo": MagicMock(),
            "mcp_center": MagicMock(),
            "mcp_config_service": MagicMock(),
            "skill_service": MagicMock(),
            "bot_repo": MagicMock(),
            "device_plugin": MagicMock(),
            "path_factory": MagicMock(),
        }

        def mock_get_bot(bot_id):
            bot = MagicMock()
            bot.get.return_value = "staff_197444"
            return bot
        mocks["bot_repo"].get_by_id = mock_get_bot

        test_path = Path("/tmp/test_failure")
        test_path.mkdir(parents=True, exist_ok=True)
        mocks["path_factory"].get_bot_skills_dir.return_value = test_path
        mocks["path_factory"].get_bot_skills_repo_dir.return_value = Path("/tmp/test_repo_failure")
        mocks["path_factory"].get_bot_skills_local_dir.return_value = Path("/tmp/test_local_failure")

        mocks["skill_set_repo"].get_by_id.return_value = {
            "id": "1",
            "name": "Test Set",
            "is_active": True,
            "is_default": False,
            "user_id": "197444",
            "bot_id": "bot_123"
        }

        skill_with_git = MagicMock()
        skill_with_git.get.side_effect = lambda k, default=None: {
            "id": "1",
            "name": "test_skill",
            "git_path": "git://business/test_skill",
            "skill_set_id": "1"
        }.get(k, default)

        mocks["skill_repo"].get_by_id.return_value = skill_with_git
        mocks["skill_repo"].list_skills.return_value = [{"id": "1", "name": "test_skill", "git_path": "git://business/test_skill"}]
        mocks["skill_set_repo"].get_skills_in_set.return_value = []

        service = SkillSetService(
            skill_repo=mocks["skill_repo"],
            skill_set_repo=mocks["skill_set_repo"],
            mcp_center=mocks["mcp_center"],
            mcp_config_service=mocks["mcp_config_service"],
            skill_service=mocks["skill_service"],
            bot_repo=mocks["bot_repo"],
            entity_id="staff_197444",
            bot_id="bot_123",
            engine_type="openclaw",
            device_plugin=mocks["device_plugin"],
            path_factory=mocks["path_factory"],
        )

        # Mock activate_skill 抛出异常
        async def mock_activate_error(*args, **kwargs):
            raise Exception("Device connection failed")

        service.skill_service.activate_skill = mock_activate_error
        service.mcp_center.get_mcp_by_skill_set.return_value = []
        service._sync_symlinks_to_device_if_needed = MagicMock(return_value=True)

        result = await service.add_skills_to_set(
            skill_set_id="1",
            skill_ids=["1"],
            user_id="197444"
        )

        assert "activation_failed" in result
        assert len(result["activation_failed"]) > 0
        assert result["activation_failed"][0]["skill_id"] == "1"
        assert "Device connection failed" in result["activation_failed"][0]["reason"]

        import shutil
        shutil.rmtree("/tmp/test_failure", ignore_errors=True)
        shutil.rmtree("/tmp/test_repo_failure", ignore_errors=True)
        shutil.rmtree("/tmp/test_local_failure", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_result_has_activation_failed_field(self):
        """测试结果中始终包含 activation_failed 字段"""
        import sys
        sys.path.insert(0, 'src')
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        mocks = {
            "skill_repo": MagicMock(),
            "skill_set_repo": MagicMock(),
            "mcp_center": MagicMock(),
            "mcp_config_service": MagicMock(),
            "skill_service": MagicMock(),
            "bot_repo": MagicMock(),
            "device_plugin": MagicMock(),
            "path_factory": MagicMock(),
        }

        mocks["skill_set_repo"].get_by_id.return_value = {
            "id": "1",
            "name": "Test Set",
            "is_active": False,  # 非激活状态
            "is_default": False,
            "user_id": "197444",
            "bot_id": "bot_123"
        }

        skill_with_git = MagicMock()
        skill_with_git.get.side_effect = lambda k, default=None: {
            "id": "1",
            "name": "test_skill",
            "git_path": "git://business/test_skill"
        }.get(k, default)

        mocks["skill_repo"].get_by_id.return_value = skill_with_git
        mocks["skill_repo"].list_skills.return_value = [{"id": "1", "name": "test_skill", "git_path": "git://business/test_skill"}]
        mocks["skill_set_repo"].get_skills_in_set.return_value = []

        test_path = Path("/tmp/test_empty")
        test_path.mkdir(parents=True, exist_ok=True)
        mocks["path_factory"].get_bot_skills_dir.return_value = test_path
        mocks["path_factory"].get_bot_skills_repo_dir.return_value = Path("/tmp/test_repo_empty")
        mocks["path_factory"].get_bot_skills_local_dir.return_value = Path("/tmp/test_local_empty")

        service = SkillSetService(
            skill_repo=mocks["skill_repo"],
            skill_set_repo=mocks["skill_set_repo"],
            mcp_center=mocks["mcp_center"],
            mcp_config_service=mocks["mcp_config_service"],
            skill_service=mocks["skill_service"],
            bot_repo=mocks["bot_repo"],
            entity_id="staff_197444",
            bot_id="bot_123",
            engine_type="openclaw",
            device_plugin=mocks["device_plugin"],
            path_factory=mocks["path_factory"],
        )

        service.mcp_center.get_mcp_by_skill_set.return_value = []
        service._sync_symlinks_to_device_if_needed = MagicMock(return_value=True)

        result = await service.add_skills_to_set(
            skill_set_id="1",
            skill_ids=["1"],
            user_id="197444"
        )

        assert "activation_failed" in result

        import shutil
        shutil.rmtree("/tmp/test_empty", ignore_errors=True)
        shutil.rmtree("/tmp/test_repo_empty", ignore_errors=True)
        shutil.rmtree("/tmp/test_local_empty", ignore_errors=True)

