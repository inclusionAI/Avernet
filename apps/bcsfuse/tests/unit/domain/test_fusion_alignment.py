"""
Tests for FusionAlignmentPoint

G2: Conflict Alignment Layer

测试 FusionAlignmentPoint 领域模型。
"""

from __future__ import annotations

import pytest


class TestFusionAlignmentModule:
    """模块存在性测试"""

    def test_module_exists(self):
        """测试 fusion_alignment 模块存在"""
        import importlib

        module = importlib.import_module("src.domain.models.fusion_alignment")
        assert module is not None

    def test_alignment_class_exists(self):
        """测试 FusionAlignmentPoint 类存在"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        assert FusionAlignmentPoint is not None


class TestFusionAlignmentPointCreation:
    """FusionAlignmentPoint 创建测试"""

    def test_alignment_creation_with_required_fields(self):
        """测试使用必填字段创建对齐点"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        alignment = FusionAlignmentPoint(
            summary="三方都认同需要兼顾用户体验和安全",
        )

        assert alignment.summary == "三方都认同需要兼顾用户体验和安全"

    def test_alignment_with_participants(self):
        """测试带参与者信息的对齐点"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        alignment = FusionAlignmentPoint(
            summary="张三、李四都同意可以分阶段实施",
            participants=["zhangsan", "lisi"],
        )

        assert alignment.summary == "张三、李四都同意可以分阶段实施"
        assert alignment.participants == ["zhangsan", "lisi"]

    def test_alignment_without_participants(self):
        """测试不带参与者信息的对齐点"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        alignment = FusionAlignmentPoint(
            summary="所有参与者都认同项目目标",
        )

        assert alignment.participants is None

    def test_alignment_empty_participants_list(self):
        """测试空参与者列表（区别于 None）"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        alignment = FusionAlignmentPoint(
            summary="通用共识",
            participants=[],
        )

        # 空列表应该被视为空，不是 None
        assert alignment.participants == []


class TestFusionAlignmentPointValidation:
    """FusionAlignmentPoint 校验测试"""

    def test_alignment_empty_summary_rejected(self):
        """测试空 summary 被拒绝"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FusionAlignmentPoint(
                summary="",  # 空
            )

    def test_alignment_summary_max_length(self):
        """测试 summary 最大长度限制"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        # 允许合理的长描述
        long_summary = "这是一个很长的共识描述。" * 50
        alignment = FusionAlignmentPoint(summary=long_summary[:500])

        assert alignment.summary == long_summary[:500]

    def test_alignment_participants_not_required(self):
        """测试 participants 不是必填"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        # 只提供 summary，不提供 participants
        alignment = FusionAlignmentPoint(summary="所有方都同意")

        assert alignment.summary == "所有方都同意"
        assert alignment.participants is None


class TestFusionAlignmentPointSerialization:
    """FusionAlignmentPoint 序列化测试"""

    def test_alignment_model_dump(self):
        """测试 model_dump 序列化"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        alignment = FusionAlignmentPoint(
            summary="技术妥协可行性高",
            participants=["dev", "pm"],
        )

        data = alignment.model_dump()

        assert data["summary"] == "技术妥协可行性高"
        assert data["participants"] == ["dev", "pm"]

    def test_alignment_json_serialization(self):
        """测试 JSON 序列化"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint
        import json

        alignment = FusionAlignmentPoint(
            summary="安全风险可通过机制补偿",
        )

        json_str = alignment.model_dump_json()
        data = json.loads(json_str)

        assert data["summary"] == "安全风险可通过机制补偿"
        assert data["participants"] is None

    def test_alignment_without_participants_serialization(self):
        """测试不包含 participants 的序列化"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        alignment = FusionAlignmentPoint(
            summary="全局共识点",
            participants=None,
        )

        data = alignment.model_dump()
        assert "participants" in data
        assert data["participants"] is None


class TestFusionAlignmentPointUseCases:
    """FusionAlignmentPoint 使用场景测试"""

    def test_simple_consensus(self):
        """测试简单共识场景"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        alignment = FusionAlignmentPoint(
            summary="所有参与者都认同项目目标",
        )

        assert "认同" in alignment.summary

    def test_partial_agreement(self):
        """测试部分同意场景"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        alignment = FusionAlignmentPoint(
            summary="开发与PM同意分两阶段实施",
            participants=["dev", "pm"],
        )

        assert len(alignment.participants) == 2

    def test_trade_off_point(self):
        """测试折中点场景"""
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        alignment = FusionAlignmentPoint(
            summary="安全团队接受分阶段改造作为折中",
            participants=["anquan"],
        )

        assert "折中" in alignment.summary