"""
Tests for File Context Loader

Worker Profile Ingestion Baseline

测试范围：
- FileContextLoader: 上下文文件加载器
- md 文件读取
- 未识别文件处理
- 空文件处理
"""

from __future__ import annotations

import os
import tempfile
import pytest


class TestFileContextLoader:
    """测试 FileContextLoader"""

    def test_load_context_files_success(self):
        """测试成功加载上下文文件"""
        from src.infra.worker_profiles.loaders.file_context_loader import (
            FileContextLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.domain.models.context_fragment import ContextKind

        # 创建临时目录和文件
        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_path = os.path.join(tmpdir, "openclaw")
            os.makedirs(openclaw_path)

            # 创建测试文件
            soul_path = os.path.join(openclaw_path, "SOUL.md")
            with open(soul_path, "w") as f:
                f.write("# Identity\nName: Test Bot\n")

            agents_path = os.path.join(openclaw_path, "AGENTS.md")
            with open(agents_path, "w") as f:
                f.write("# Agent Configuration\n")

            # 加载文件
            settings = WorkerProfileSettings()
            loader = FileContextLoader(settings)
            fragments = loader.load(openclaw_path)

            # 验证
            assert len(fragments) == 2

            # 按 kind 排序找到对应 fragment
            soul_fragment = next(
                (f for f in fragments if f.kind == ContextKind.SOUL), None
            )
            agent_fragment = next(
                (f for f in fragments if f.kind == ContextKind.AGENT), None
            )

            assert soul_fragment is not None
            assert "Identity" in soul_fragment.content
            assert soul_fragment.filename == "SOUL.md"

            assert agent_fragment is not None
            assert "Configuration" in agent_fragment.content
            assert agent_fragment.filename == "AGENTS.md"

    def test_load_context_files_missing_directory(self):
        """测试目录不存在的处理"""
        from src.infra.worker_profiles.loaders.file_context_loader import (
            FileContextLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings()
        loader = FileContextLoader(settings)

        # 目录不存在应返回空列表
        fragments, warnings = loader.load_with_warnings("/nonexistent/path")

        assert fragments == []
        assert len(warnings) == 1
        assert "not found" in warnings[0].message.lower() or "not exist" in warnings[0].message.lower()

    def test_load_context_files_empty_directory(self):
        """测试空目录的处理"""
        from src.infra.worker_profiles.loaders.file_context_loader import (
            FileContextLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_path = os.path.join(tmpdir, "openclaw")
            os.makedirs(openclaw_path)

            settings = WorkerProfileSettings()
            loader = FileContextLoader(settings)
            fragments = loader.load(openclaw_path)

            assert fragments == []

    def test_load_context_files_empty_file(self):
        """测试空文件的处理"""
        from src.infra.worker_profiles.loaders.file_context_loader import (
            FileContextLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.domain.models.context_fragment import ContextKind

        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_path = os.path.join(tmpdir, "openclaw")
            os.makedirs(openclaw_path)

            # 创建空文件
            soul_path = os.path.join(openclaw_path, "SOUL.md")
            with open(soul_path, "w") as f:
                f.write("")

            settings = WorkerProfileSettings()
            loader = FileContextLoader(settings)
            fragments = loader.load(openclaw_path)

            # 空文件也应该加载
            assert len(fragments) == 1
            assert fragments[0].content == ""
            assert fragments[0].kind == ContextKind.SOUL

    def test_load_context_files_unrecognized_md(self):
        """测试未识别的 md 文件处理"""
        from src.infra.worker_profiles.loaders.file_context_loader import (
            FileContextLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.domain.models.context_fragment import ContextKind

        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_path = os.path.join(tmpdir, "openclaw")
            os.makedirs(openclaw_path)

            # 创建未识别的 md 文件
            custom_path = os.path.join(openclaw_path, "CUSTOM.md")
            with open(custom_path, "w") as f:
                f.write("# Custom Content\n")

            settings = WorkerProfileSettings()
            loader = FileContextLoader(settings)
            fragments, warnings = loader.load_with_warnings(openclaw_path)

            # 未识别的文件应该被加载为 OTHER 类型
            assert len(fragments) == 1
            assert fragments[0].kind == ContextKind.OTHER
            assert fragments[0].filename == "CUSTOM.md"

            # 应该有警告
            assert any("unrecognized" in w.message.lower() or "other" in w.message.lower()
                       for w in warnings)

    def test_load_context_files_non_md_ignored(self):
        """测试非 md 文件被忽略"""
        from src.infra.worker_profiles.loaders.file_context_loader import (
            FileContextLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_path = os.path.join(tmpdir, "openclaw")
            os.makedirs(openclaw_path)

            # 创建 md 文件
            soul_path = os.path.join(openclaw_path, "SOUL.md")
            with open(soul_path, "w") as f:
                f.write("# Test")

            # 创建非 md 文件
            txt_path = os.path.join(openclaw_path, "README.txt")
            with open(txt_path, "w") as f:
                f.write("This should be ignored")

            json_path = os.path.join(openclaw_path, "config.json")
            with open(json_path, "w") as f:
                f.write("{}")

            settings = WorkerProfileSettings()
            loader = FileContextLoader(settings)
            fragments = loader.load(openclaw_path)

            # 只有 md 文件被加载
            assert len(fragments) == 1
            assert fragments[0].filename == "SOUL.md"

    def test_load_context_files_preserves_source_path(self):
        """测试保留源文件路径"""
        from src.infra.worker_profiles.loaders.file_context_loader import (
            FileContextLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_path = os.path.join(tmpdir, "openclaw")
            os.makedirs(openclaw_path)

            soul_path = os.path.join(openclaw_path, "SOUL.md")
            with open(soul_path, "w") as f:
                f.write("# Test")

            settings = WorkerProfileSettings()
            loader = FileContextLoader(settings)
            fragments = loader.load(openclaw_path)

            # 源路径应该是绝对路径
            assert os.path.isabs(fragments[0].source_path)
            assert fragments[0].source_path == soul_path


class TestFileContextLoaderCustomMapping:
    """测试自定义文件映射"""

    def test_custom_mapping_applied(self):
        """测试自定义映射生效"""
        from src.infra.worker_profiles.loaders.file_context_loader import (
            FileContextLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.domain.models.context_fragment import ContextKind

        with tempfile.TemporaryDirectory() as tmpdir:
            openclaw_path = os.path.join(tmpdir, "openclaw")
            os.makedirs(openclaw_path)

            # 创建自定义文件
            custom_path = os.path.join(openclaw_path, "MYCUSTOM.md")
            with open(custom_path, "w") as f:
                f.write("# Custom")

            # 自定义映射
            settings = WorkerProfileSettings(
                context_file_mapping={
                    "MYCUSTOM.md": ContextKind.SOUL,
                }
            )
            loader = FileContextLoader(settings)
            fragments = loader.load(openclaw_path)

            # 应该使用自定义映射
            assert len(fragments) == 1
            assert fragments[0].kind == ContextKind.SOUL
            assert fragments[0].filename == "MYCUSTOM.md"