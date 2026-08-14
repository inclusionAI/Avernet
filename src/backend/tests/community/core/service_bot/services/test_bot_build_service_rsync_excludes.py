"""测试 BotBuildService.build() 从 bot.ext 读取并传递 rsync excludes 配置。

覆盖 bot_build_service.py 第 224-229 行的变更：
- 从 bot.ext 解析 build_rsync_excludes 配置
- 调用 parse_build_rsync_excludes_from_ext(ext)
- 将解析结果传递给 provider.get_build_plan(build_rsync_excludes_append=...)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildService,
)
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan


def _make_service() -> BotBuildService:
    """构造一个 BotBuildService 实例，绕过 @inject。

    只设置 build 方法直接依赖的字段。
    """
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()
    return service


@pytest.mark.unit
class TestBotBuildServiceRsyncExcludesConfig:
    """测试 Bot 级别 rsync excludes 配置的读取和传递。"""

    def test_build_passes_ext_config_to_provider(self):
        """验证 build() 方法从 bot.ext 读取配置并传递给 provider。"""
        # 准备测试数据
        bot = {
            "bot_id": "test-bot-id",
            "entity_id": "test-entity-id",
            "entity_type": "staff",
            "device_id": "test-device-id",
            "ext": {
                "build_rsync_excludes": ["custom_exclude/", "another_exclude"]
            },
        }

        # Mock provider 和其返回的 build_plan
        mock_provider = MagicMock()
        mock_build_plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name=".openclaw",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["workspace/memory/", "logs/"],
        )
        mock_provider.get_build_plan.return_value = mock_build_plan

        # Mock BotBuildService
        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=mock_provider)
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)
        service._get_migration_path_base = MagicMock(return_value="/fake/path")

        # 执行 build
        try:
            service.build(bot, version=1)
        except Exception:
            # build() 方法可能因为 mock 不完整而失败
            # 但我们只关心 provider.get_build_plan 的调用
            pass

        # 验证 provider.get_build_plan 被正确调用
        mock_provider.get_build_plan.assert_called_once()
        call_kwargs = mock_provider.get_build_plan.call_args[1]

        # 验证传递的参数是解析后的 rsync excludes 列表
        assert "build_rsync_excludes_append" in call_kwargs
        assert call_kwargs["build_rsync_excludes_append"] == [
            "custom_exclude/",
            "another_exclude",
        ]

    def test_build_handles_none_ext(self):
        """验证 bot.ext 为 None 时使用默认值。"""
        bot = {
            "bot_id": "test-bot-id",
            "entity_id": "test-entity-id",
            "entity_type": "staff",
            "device_id": "test-device-id",
            "ext": None,  # ext 为 None
        }

        mock_provider = MagicMock()
        mock_build_plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name=".openclaw",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["workspace/memory/", "logs/"],
        )
        mock_provider.get_build_plan.return_value = mock_build_plan

        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=mock_provider)
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)
        service._get_migration_path_base = MagicMock(return_value="/fake/path")

        try:
            service.build(bot, version=1)
        except Exception:
            pass

        # 验证传递 None 给 get_build_plan
        mock_provider.get_build_plan.assert_called_once()
        call_kwargs = mock_provider.get_build_plan.call_args[1]
        assert call_kwargs["build_rsync_excludes_append"] is None

    def test_build_handles_missing_ext_key(self):
        """验证 bot 缺少 ext 字段时使用默认值。"""
        bot = {
            "bot_id": "test-bot-id",
            "entity_id": "test-entity-id",
            "entity_type": "staff",
            "device_id": "test-device-id",
            # 缺少 ext 字段
        }

        mock_provider = MagicMock()
        mock_build_plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name=".openclaw",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["workspace/memory/", "logs/"],
        )
        mock_provider.get_build_plan.return_value = mock_build_plan

        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=mock_provider)
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)
        service._get_migration_path_base = MagicMock(return_value="/fake/path")

        try:
            service.build(bot, version=1)
        except Exception:
            pass

        # 验证传递 None 给 get_build_plan
        mock_provider.get_build_plan.assert_called_once()
        call_kwargs = mock_provider.get_build_plan.call_args[1]
        assert call_kwargs["build_rsync_excludes_append"] is None

    def test_build_empty_ext_build_rsync_excludes(self):
        """验证 ext.build_rsync_excludes 为空列表时使用默认值。"""
        bot = {
            "bot_id": "test-bot-id",
            "entity_id": "test-entity-id",
            "entity_type": "staff",
            "device_id": "test-device-id",
            "ext": {
                "build_rsync_excludes": []  # 空列表
            },
        }

        mock_provider = MagicMock()
        mock_build_plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name=".openclaw",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["workspace/memory/", "logs/"],
        )
        mock_provider.get_build_plan.return_value = mock_build_plan

        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=mock_provider)
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)
        service._get_migration_path_base = MagicMock(return_value="/fake/path")

        try:
            service.build(bot, version=1)
        except Exception:
            pass

        # 空列表被视为 falsy，parse_build_rsync_excludes_from_ext 返回 None
        mock_provider.get_build_plan.assert_called_once()
        call_kwargs = mock_provider.get_build_plan.call_args[1]
        assert call_kwargs["build_rsync_excludes_append"] is None