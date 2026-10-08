"""
Tests for FusionConflict

G2: Conflict Alignment Layer

测试 FusionConflict 领域模型。
"""

from __future__ import annotations

import pytest


class TestFusionConflictModule:
    """模块存在性测试"""

    def test_module_exists(self):
        """测试 fusion_conflict 模块存在"""
        import importlib

        module = importlib.import_module("src.domain.models.fusion_conflict")
        assert module is not None

    def test_conflict_class_exists(self):
        """测试 FusionConflict 类存在"""
        from src.domain.models.fusion_conflict import FusionConflict

        assert FusionConflict is not None


class TestFusionConflictCreation:
    """FusionConflict 创建测试"""

    def test_conflict_creation_with_required_fields(self):
        """测试使用必填字段创建冲突"""
        from src.domain.models.fusion_conflict import FusionConflict

        conflict = FusionConflict(
            parties=["zhangsan", "lisi"],
            issue="超时时间不一致",
            positions=["60分钟（兼容）", "30分钟（PRD）"],
            severity="medium",
        )

        assert conflict.parties == ["zhangsan", "lisi"]
        assert conflict.issue == "超时时间不一致"
        assert conflict.positions == ["60分钟（兼容）", "30分钟（PRD）"]
        assert conflict.severity == "medium"

    def test_conflict_with_multiple_parties(self):
        """测试多方冲突创建"""
        from src.domain.models.fusion_conflict import FusionConflict

        conflict = FusionConflict(
            parties=["zhangsan", "lisi", "anquan"],
            issue="上线标准不一致",
            positions=["可上线", "需修改", "不通过"],
            severity="high",
        )

        assert len(conflict.parties) == 3
        assert len(conflict.positions) == 3

    def test_conflict_all_severity_levels(self):
        """测试所有严重级别"""
        from src.domain.models.fusion_conflict import FusionConflict, Severity

        for severity in ["low", "medium", "high", "critical"]:
            conflict = FusionConflict(
                parties=["a", "b"],
                issue=f"test issue with {severity}",
                positions=["pos1", "pos2"],
                severity=severity,  # type: ignore
            )
            assert conflict.severity == severity


class TestFusionConflictValidation:
    """FusionConflict 校验测试"""

    def test_conflict_parties_min_length(self):
        """测试 parties 最少需要 2 个"""
        from src.domain.models.fusion_conflict import FusionConflict
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FusionConflict(
                parties=["zhangsan"],  # 只有一个
                issue="test",
                positions=["pos1"],
                severity="low",
            )

    def test_conflict_positions_match_parties(self):
        """测试 positions 数量与 parties 匹配或更多上下文"""
        from src.domain.models.fusion_conflict import FusionConflict

        # positions 可以直接描述各方立场
        conflict = FusionConflict(
            parties=["a", "b"],
            issue="test",
            positions=["立场A", "立场B"],
            severity="medium",
        )
        assert len(conflict.positions) >= 2

    def test_conflict_invalid_severity_rejected(self):
        """测试无效严重级别被拒绝"""
        from src.domain.models.fusion_conflict import FusionConflict
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FusionConflict(
                parties=["a", "b"],
                issue="test",
                positions=["p1", "p2"],
                severity="invalid",  # type: ignore
            )

    def test_conflict_empty_issue_rejected(self):
        """测试空 issue 被拒绝"""
        from src.domain.models.fusion_conflict import FusionConflict
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FusionConflict(
                parties=["a", "b"],
                issue="",  # 空
                positions=["p1", "p2"],
                severity="low",
            )


class TestFusionConflictSerialization:
    """FusionConflict 序列化测试"""

    def test_conflict_model_dump(self):
        """测试 model_dump 序列化"""
        from src.domain.models.fusion_conflict import FusionConflict

        conflict = FusionConflict(
            parties=["zhangsan", "lisi"],
            issue="超时时间不一致",
            positions=["60分钟", "30分钟"],
            severity="medium",
        )

        data = conflict.model_dump()

        assert data["parties"] == ["zhangsan", "lisi"]
        assert data["issue"] == "超时时间不一致"
        assert data["positions"] == ["60分钟", "30分钟"]
        assert data["severity"] == "medium"

    def test_conflict_json_serialization(self):
        """测试 JSON 序列化"""
        from src.domain.models.fusion_conflict import FusionConflict
        import json

        conflict = FusionConflict(
            parties=["a", "b"],
            issue="test",
            positions=["p1", "p2"],
            severity="high",
        )

        # 序列化为 JSON
        json_str = conflict.model_dump_json()
        data = json.loads(json_str)

        assert data["parties"] == ["a", "b"]
        assert data["severity"] == "high"


class TestSeverityEnum:
    """Severity 枚举测试"""

    def test_severity_enum_exists(self):
        """测试 Severity 枚举存在"""
        from src.domain.models.fusion_conflict import Severity

        assert Severity is not None

    def test_severity_enum_values(self):
        """测试 Severity 枚举值"""
        from src.domain.models.fusion_conflict import Severity

        assert Severity.LOW.value == "low"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.HIGH.value == "high"
        assert Severity.CRITICAL.value == "critical"