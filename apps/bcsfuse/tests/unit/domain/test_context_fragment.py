"""
Tests for Context Fragment Domain Model

Worker Profile Ingestion Baseline

测试范围：
- ContextKind: 上下文类型枚举
- ContextFragment: 上下文片段模型
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestContextKind:
    """测试 ContextKind 枚举"""

    def test_context_kind_values(self):
        """测试枚举值定义"""
        from src.domain.models.context_fragment import ContextKind

        assert ContextKind.AGENT == "agent"
        assert ContextKind.BOOT == "boot"
        assert ContextKind.HEARTBEAT == "heartbeat"
        assert ContextKind.SOUL == "soul"
        assert ContextKind.TOOLS == "tools"
        assert ContextKind.RULES == "rules"
        assert ContextKind.MEMORY == "memory"
        assert ContextKind.USER == "user"
        assert ContextKind.OTHER == "other"

    def test_context_kind_from_string(self):
        """测试从字符串创建枚举"""
        from src.domain.models.context_fragment import ContextKind

        assert ContextKind("agent") == ContextKind.AGENT
        assert ContextKind("soul") == ContextKind.SOUL
        assert ContextKind("other") == ContextKind.OTHER

    def test_context_kind_invalid_value(self):
        """测试无效枚举值"""
        from src.domain.models.context_fragment import ContextKind

        with pytest.raises(ValueError):
            ContextKind("invalid_kind")


class TestContextFragment:
    """测试 ContextFragment 模型"""

    def test_create_context_fragment_success(self):
        """测试创建上下文片段"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="# Identity\nName: Test Bot",
            source_path="/data/staff_001/default/openclaw/SOUL.md",
        )

        assert fragment.kind == ContextKind.SOUL
        assert fragment.filename == "SOUL.md"
        assert fragment.content == "# Identity\nName: Test Bot"
        assert fragment.source_path == "/data/staff_001/default/openclaw/SOUL.md"
        assert fragment.weight == 1.0  # 默认值
        assert fragment.metadata == {}  # 默认值

    def test_create_context_fragment_with_all_fields(self):
        """测试创建包含所有字段的上下文片段"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        fragment = ContextFragment(
            kind=ContextKind.AGENT,
            filename="AGENTS.md",
            content="# Agent Config",
            source_path="/data/staff_001/default/openclaw/AGENTS.md",
            weight=0.8,
            metadata={"lines": 10, "author": "system"},
        )

        assert fragment.kind == ContextKind.AGENT
        assert fragment.weight == 0.8
        assert fragment.metadata["lines"] == 10
        assert fragment.metadata["author"] == "system"

    def test_create_context_fragment_empty_content(self):
        """测试创建空内容的上下文片段（允许空内容）"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        # 空内容是允许的，表示文件存在但为空
        fragment = ContextFragment(
            kind=ContextKind.BOOT,
            filename="BOOT.md",
            content="",
            source_path="/data/staff_001/default/openclaw/BOOT.md",
        )

        assert fragment.content == ""

    def test_create_context_fragment_with_other_kind(self):
        """测试创建 OTHER 类型的上下文片段"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        fragment = ContextFragment(
            kind=ContextKind.OTHER,
            filename="CUSTOM.md",
            content="# Custom content",
            source_path="/data/staff_001/default/openclaw/CUSTOM.md",
        )

        assert fragment.kind == ContextKind.OTHER
        assert fragment.filename == "CUSTOM.md"

    def test_weight_must_be_between_0_and_1(self):
        """测试权重必须在 0-1 之间"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        # 有效权重
        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="test",
            source_path="/test",
            weight=0.5,
        )
        assert fragment.weight == 0.5

        # 最小值
        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="test",
            source_path="/test",
            weight=0.0,
        )
        assert fragment.weight == 0.0

        # 最大值
        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="test",
            source_path="/test",
            weight=1.0,
        )
        assert fragment.weight == 1.0

    def test_weight_out_of_range_raises_error(self):
        """测试权重超出范围抛出错误"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        with pytest.raises(ValidationError):
            ContextFragment(
                kind=ContextKind.SOUL,
                filename="SOUL.md",
                content="test",
                source_path="/test",
                weight=1.5,  # 超过 1
            )

        with pytest.raises(ValidationError):
            ContextFragment(
                kind=ContextKind.SOUL,
                filename="SOUL.md",
                content="test",
                source_path="/test",
                weight=-0.1,  # 小于 0
            )

    def test_missing_required_fields_raises_error(self):
        """测试缺少必填字段抛出错误"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        # 缺少 kind
        with pytest.raises(ValidationError):
            ContextFragment(
                filename="SOUL.md",
                content="test",
                source_path="/test",
            )

        # 缺少 filename
        with pytest.raises(ValidationError):
            ContextFragment(
                kind=ContextKind.SOUL,
                content="test",
                source_path="/test",
            )

        # 缺少 source_path
        with pytest.raises(ValidationError):
            ContextFragment(
                kind=ContextKind.SOUL,
                filename="SOUL.md",
                content="test",
            )

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        with pytest.raises(ValidationError) as exc_info:
            ContextFragment(
                kind=ContextKind.SOUL,
                filename="SOUL.md",
                content="test",
                source_path="/test",
                extra_field="not_allowed",  # type: ignore
            )

        assert "extra" in str(exc_info.value).lower()

    def test_content_can_be_whitespace(self):
        """测试内容可以全是空白（与 MarkdownDocument 不同）"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        # ContextFragment 允许空白内容，因为文件可能为空
        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="   \n\t  ",
            source_path="/test",
        )

        assert fragment.content == "   \n\t  "


class TestContextFragmentProperty:
    """测试 ContextFragment 属性方法"""

    def test_is_empty_true(self):
        """测试 is_empty 属性为 True"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="",
            source_path="/test",
        )

        assert fragment.is_empty is True

    def test_is_empty_false(self):
        """测试 is_empty 属性为 False"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="Some content",
            source_path="/test",
        )

        assert fragment.is_empty is False

    def test_content_preview(self):
        """测试 content_preview 属性"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        # 短内容
        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="Short content",
            source_path="/test",
        )
        assert fragment.content_preview == "Short content"

        # 长内容截断
        long_content = "x" * 500
        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content=long_content,
            source_path="/test",
        )
        assert len(fragment.content_preview) == 200
        assert fragment.content_preview == "x" * 200

    def test_content_preview_empty(self):
        """测试空内容的 content_preview"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="",
            source_path="/test",
        )

        assert fragment.content_preview == ""