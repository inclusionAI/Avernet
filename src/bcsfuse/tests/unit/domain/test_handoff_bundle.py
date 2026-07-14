"""
Tests for HandoffBundle Domain Model

M9: OpenClaw Adapter

测试 HandoffBundle、HandoffFile 和 Manifest 模型的构造、字段校验和行为。
"""

from __future__ import annotations

import pytest

from src.domain.models.handoff_bundle import (
    HandoffBundle,
    HandoffFile,
    Manifest,
)


# =============================================================================
# HandoffFile Tests
# =============================================================================

class TestHandoffFile:
    """HandoffFile 测试"""

    def test_create_handoff_file(self):
        """测试创建 HandoffFile"""
        file = HandoffFile(
            filename="TASK.md",
            content="# TASK\n\n## Goal\nTest goal",
            content_type="markdown",
        )

        assert file.filename == "TASK.md"
        assert "TASK" in file.content
        assert file.content_type == "markdown"

    def test_handoff_file_default_content_type(self):
        """测试默认 content_type"""
        file = HandoffFile(
            filename="test.md",
            content="test content",
        )

        assert file.content_type == "markdown"

    def test_handoff_file_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            HandoffFile(
                filename="test.md",
                content="test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# Manifest Tests
# =============================================================================

class TestManifest:
    """Manifest 测试"""

    def test_create_manifest(self):
        """测试创建 Manifest"""
        manifest = Manifest(
            task_id="tsk_001",
            team_id="team_001",
            generated_at="2026-03-21T00:00:00Z",
            files=["TASK.md", "TEAM.md"],
            skills_enabled=["skill_001"],
            resources_enabled=["res_001"],
        )

        assert manifest.task_id == "tsk_001"
        assert manifest.team_id == "team_001"
        assert manifest.generated_at == "2026-03-21T00:00:00Z"
        assert len(manifest.files) == 2
        assert len(manifest.skills_enabled) == 1
        assert len(manifest.resources_enabled) == 1

    def test_manifest_default_values(self):
        """测试默认值"""
        manifest = Manifest(
            task_id="tsk_001",
            generated_at="2026-03-21T00:00:00Z",
        )

        assert manifest.team_id is None
        assert manifest.files == []
        assert manifest.skills_enabled == []
        assert manifest.resources_enabled == []

    def test_manifest_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            Manifest(
                task_id="tsk_001",
                generated_at="2026-03-21T00:00:00Z",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# HandoffBundle Tests
# =============================================================================

class TestHandoffBundle:
    """HandoffBundle 测试"""

    def test_create_handoff_bundle(self):
        """测试创建 HandoffBundle"""
        files = [
            HandoffFile(filename="TASK.md", content="# TASK"),
            HandoffFile(filename="TEAM.md", content="# TEAM"),
        ]
        manifest = Manifest(
            task_id="tsk_001",
            generated_at="2026-03-21T00:00:00Z",
            files=["TASK.md", "TEAM.md"],
        )

        bundle = HandoffBundle(
            files=files,
            manifest=manifest,
        )

        assert len(bundle.files) == 2
        assert bundle.manifest == manifest

    def test_handoff_bundle_get_file(self):
        """测试获取文件"""
        files = [
            HandoffFile(filename="TASK.md", content="# TASK"),
            HandoffFile(filename="TEAM.md", content="# TEAM"),
        ]
        manifest = Manifest(
            task_id="tsk_001",
            generated_at="2026-03-21T00:00:00Z",
        )

        bundle = HandoffBundle(files=files, manifest=manifest)

        task_file = bundle.get_file("TASK.md")
        assert task_file is not None
        assert task_file.content == "# TASK"

        missing_file = bundle.get_file("MISSING.md")
        assert missing_file is None

    def test_handoff_bundle_has_file(self):
        """测试检查文件是否存在"""
        files = [
            HandoffFile(filename="TASK.md", content="# TASK"),
        ]
        manifest = Manifest(
            task_id="tsk_001",
            generated_at="2026-03-21T00:00:00Z",
        )

        bundle = HandoffBundle(files=files, manifest=manifest)

        assert bundle.has_file("TASK.md") is True
        assert bundle.has_file("MISSING.md") is False

    def test_handoff_bundle_file_names(self):
        """测试获取文件名列表"""
        files = [
            HandoffFile(filename="TASK.md", content="# TASK"),
            HandoffFile(filename="TEAM.md", content="# TEAM"),
        ]
        manifest = Manifest(
            task_id="tsk_001",
            generated_at="2026-03-21T00:00:00Z",
        )

        bundle = HandoffBundle(files=files, manifest=manifest)

        assert set(bundle.file_names) == {"TASK.md", "TEAM.md"}

    def test_handoff_bundle_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        manifest = Manifest(
            task_id="tsk_001",
            generated_at="2026-03-21T00:00:00Z",
        )

        with pytest.raises(Exception):  # ValidationError
            HandoffBundle(
                files=[],
                manifest=manifest,
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# Required Files Tests
# =============================================================================

class TestHandoffBundleRequiredFiles:
    """必需文件测试"""

    def test_bundle_has_required_files(self):
        """测试 bundle 包含所有必需文件"""
        required_files = [
            "TASK.md",
            "TEAM.md",
            "CONTEXT.md",
            "RESOURCES.md",
            "GUARDRAILS.md",
            "AGENTS.md",
            "SOUL.md",
            "TOOLS.md",
        ]

        files = [
            HandoffFile(filename=filename, content=f"# {filename}")
            for filename in required_files
        ]
        manifest = Manifest(
            task_id="tsk_001",
            generated_at="2026-03-21T00:00:00Z",
            files=required_files,
        )

        bundle = HandoffBundle(files=files, manifest=manifest)

        for filename in required_files:
            assert bundle.has_file(filename), f"Missing required file: {filename}"