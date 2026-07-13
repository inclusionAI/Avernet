"""
Tests for Worker Profile Settings

Worker Profile Ingestion Baseline

测试范围：
- WorkerProfileSettings: 配置模型
- 多 roots 支持
- 环境变量加载
- 默认值
- context_file_mapping 扩展
"""

from __future__ import annotations

import os
import pytest
from pydantic import ValidationError


class TestWorkerProfileSettings:
    """测试 WorkerProfileSettings 模型"""

    def test_create_settings_with_defaults(self):
        """测试使用默认值创建配置"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings()

        # 默认 roots
        assert settings.roots == ["/aidesktop/aidesktop_pre/bolt_data"]

        # 默认选项
        assert settings.include_backup is False
        assert settings.scan_bots is True
        assert settings.prefer_default is True
        assert settings.allow_no_active_skillset is False

    def test_create_settings_with_single_root(self):
        """测试单路径配置"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(roots=["/data/custom_root"])

        assert len(settings.roots) == 1
        assert settings.roots[0] == "/data/custom_root"

    def test_create_settings_with_multiple_roots(self):
        """测试多路径配置"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(
            roots=[
                "/aidesktop/aidesktop_pre/bolt_data",
                "/data/otherclaw/worker_profiles",
                "./tests/fixtures/worker_profile_source",
            ]
        )

        assert len(settings.roots) == 3
        assert "/aidesktop/aidesktop_pre/bolt_data" in settings.roots
        assert "/data/otherclaw/worker_profiles" in settings.roots

    def test_create_settings_with_all_options(self):
        """测试所有配置选项"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(
            roots=["/data/root"],
            include_backup=True,
            scan_bots=False,
            prefer_default=False,
            allow_no_active_skillset=True,
        )

        assert settings.include_backup is True
        assert settings.scan_bots is False
        assert settings.prefer_default is False
        assert settings.allow_no_active_skillset is True

    def test_default_context_file_mapping(self):
        """测试默认的上下文文件映射"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.domain.models.context_fragment import ContextKind

        settings = WorkerProfileSettings()

        mapping = settings.context_file_mapping

        # 验证默认映射
        assert mapping.get("AGENTS.md") == ContextKind.AGENT
        assert mapping.get("BOOT.md") == ContextKind.BOOT
        assert mapping.get("HEARTBEAT.md") == ContextKind.HEARTBEAT
        assert mapping.get("SOUL.md") == ContextKind.SOUL
        assert mapping.get("TOOLS.md") == ContextKind.TOOLS
        assert mapping.get("RULES.md") == ContextKind.RULES
        assert mapping.get("MEMORY.md") == ContextKind.MEMORY
        assert mapping.get("USER.md") == ContextKind.USER

    def test_custom_context_file_mapping(self):
        """测试自定义上下文文件映射"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.domain.models.context_fragment import ContextKind

        custom_mapping = {
            "AGENTS.md": ContextKind.AGENT,
            "SOUL.md": ContextKind.SOUL,
            "CUSTOM.md": ContextKind.OTHER,  # 自定义文件
        }

        settings = WorkerProfileSettings(context_file_mapping=custom_mapping)

        assert settings.context_file_mapping.get("CUSTOM.md") == ContextKind.OTHER

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with pytest.raises(ValidationError):
            WorkerProfileSettings(
                roots=["/data"],
                extra_field="not_allowed",  # type: ignore
            )


class TestWorkerProfileSettingsEnvLoading:
    """测试 WorkerProfileSettings 环境变量加载"""

    def test_load_single_root_from_env(self, monkeypatch):
        """测试从环境变量加载单路径"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        # 设置环境变量
        monkeypatch.setenv("WORKER_PROFILE_ROOTS", "/data/custom_root")

        # 重新创建配置
        settings = WorkerProfileSettings()

        assert settings.roots == ["/data/custom_root"]

    def test_load_multiple_roots_from_env(self, monkeypatch):
        """测试从环境变量加载多路径（逗号分隔）"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        # 设置环境变量（逗号分隔）
        monkeypatch.setenv(
            "WORKER_PROFILE_ROOTS",
            "/data/root1,/data/root2,/data/root3"
        )

        settings = WorkerProfileSettings()

        assert len(settings.roots) == 3
        assert "/data/root1" in settings.roots
        assert "/data/root2" in settings.roots
        assert "/data/root3" in settings.roots

    def test_load_roots_with_spaces_from_env(self, monkeypatch):
        """测试从环境变量加载带空格的路径"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        # 设置环境变量（带空格的逗号分隔）
        monkeypatch.setenv(
            "WORKER_PROFILE_ROOTS",
            "  /data/root1  ,  /data/root2  "
        )

        settings = WorkerProfileSettings()

        # 应该去除空格
        assert settings.roots == ["/data/root1", "/data/root2"]

    def test_load_boolean_options_from_env(self, monkeypatch):
        """测试从环境变量加载布尔选项"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        monkeypatch.setenv("WORKER_PROFILE_INCLUDE_BACKUP", "true")
        monkeypatch.setenv("WORKER_PROFILE_SCAN_BOTS", "false")
        monkeypatch.setenv("WORKER_PROFILE_PREFER_DEFAULT", "false")
        monkeypatch.setenv("WORKER_PROFILE_ALLOW_NO_ACTIVE_SKILLSET", "true")

        settings = WorkerProfileSettings()

        assert settings.include_backup is True
        assert settings.scan_bots is False
        assert settings.prefer_default is False
        assert settings.allow_no_active_skillset is True

    def test_load_boolean_options_case_insensitive(self, monkeypatch):
        """测试布尔选项大小写不敏感"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        monkeypatch.setenv("WORKER_PROFILE_INCLUDE_BACKUP", "TRUE")
        monkeypatch.setenv("WORKER_PROFILE_SCAN_BOTS", "FALSE")

        settings = WorkerProfileSettings()

        assert settings.include_backup is True
        assert settings.scan_bots is False

    def test_explicit_values_override_env(self, monkeypatch):
        """测试显式值覆盖环境变量"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        monkeypatch.setenv("WORKER_PROFILE_ROOTS", "/env/root")

        # 显式传入的值应该覆盖环境变量
        settings = WorkerProfileSettings(roots=["/explicit/root"])

        assert settings.roots == ["/explicit/root"]

    def test_empty_env_uses_default(self, monkeypatch):
        """测试空环境变量使用默认值"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        # 设置空值
        monkeypatch.setenv("WORKER_PROFILE_ROOTS", "")

        # 应该使用默认值
        settings = WorkerProfileSettings()
        assert settings.roots == ["/aidesktop/aidesktop_pre/bolt_data"]


class TestWorkerProfileSettingsValidation:
    """测试 WorkerProfileSettings 验证"""

    def test_empty_roots_uses_default(self):
        """测试空 roots 使用默认值"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        # 空列表应使用默认值
        settings = WorkerProfileSettings(roots=[])
        assert settings.roots == ["/aidesktop/aidesktop_pre/bolt_data"]

    def test_empty_root_string_ignored(self):
        """测试空字符串路径被忽略"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        # 包含空字符串的路径应该被过滤
        settings = WorkerProfileSettings(roots=["/valid", "", "  ", "/another"])

        # 空字符串和空格应该被过滤
        assert "" not in settings.roots
        assert "  " not in settings.roots


class TestWorkerProfileSettingsBotPattern:
    """测试 Bot 目录识别模式"""

    def test_default_bot_pattern(self):
        """测试默认的 bot 目录模式"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings()

        # 默认模式：YYYYMMDD_xxxxxxxx
        pattern = settings.bot_directory_pattern

        import re
        assert pattern is not None

        # 验证模式匹配
        assert re.match(pattern, "20260319_qjmzo9k6")
        assert re.match(pattern, "20250101_abc12345")
        assert not re.match(pattern, "default")
        assert not re.match(pattern, "default_bak")
        assert not re.match(pattern, "random_name")

    def test_custom_bot_pattern(self):
        """测试自定义 bot 目录模式"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(
            bot_directory_pattern=r"^bot_.*$"
        )

        import re
        assert re.match(settings.bot_directory_pattern, "bot_custom")
        assert not re.match(settings.bot_directory_pattern, "20260319_qjmzo9k6")


class TestWorkerProfileSettingsBackupPattern:
    """测试备份目录识别"""

    def test_default_backup_directories(self):
        """测试默认的备份目录列表"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings()

        # 默认备份目录
        assert "default_bak" in settings.backup_directories
        assert ".bak" in settings.backup_directories
        assert "_bak" in settings.backup_directories

    def test_custom_backup_directories(self):
        """测试自定义备份目录"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(
            backup_directories=["backup", "old", ".backup"]
        )

        assert "backup" in settings.backup_directories
        assert "default_bak" not in settings.backup_directories

    def test_is_backup_directory(self):
        """测试判断是否为备份目录"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings()

        assert settings.is_backup_directory("default_bak") is True
        assert settings.is_backup_directory(".bak") is True
        assert settings.is_backup_directory("_bak") is True
        assert settings.is_backup_directory("default") is False
        assert settings.is_backup_directory("20260319_abc123") is False