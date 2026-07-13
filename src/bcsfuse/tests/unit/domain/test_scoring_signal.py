"""
Tests for Scoring Signal

Worker Profile Retrieval & Fusion Simulation Baseline

测试范围：
- ScoringSignal: 打分信号模型
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestScoringSignal:
    """测试 ScoringSignal 模型"""

    def test_create_scoring_signal_success(self):
        """测试创建打分信号"""
        from src.domain.models.scoring_signal import ScoringSignal

        signal = ScoringSignal(
            signal_type="context_match",
            raw_score=0.8,
            weight=0.25,
            details={"matched_tokens": 5, "total_tokens": 10},
        )

        assert signal.signal_type == "context_match"
        assert signal.raw_score == 0.8
        assert signal.weight == 0.25
        assert signal.weighted_score == 0.2  # 0.8 * 0.25
        assert signal.details["matched_tokens"] == 5

    def test_create_scoring_signal_with_all_fields(self):
        """测试创建包含所有字段的打分信号"""
        from src.domain.models.scoring_signal import ScoringSignal

        signal = ScoringSignal(
            signal_type="skill_name_match",
            raw_score=1.0,
            weight=0.20,
            weighted_score=0.20,  # 可显式指定
            details={
                "skill_name": "web_search",
                "query_tokens": ["search", "web"],
            },
        )

        assert signal.signal_type == "skill_name_match"
        assert signal.raw_score == 1.0
        assert signal.weighted_score == 0.20

    def test_weighted_score_auto_calculated(self):
        """测试加权分数自动计算"""
        from src.domain.models.scoring_signal import ScoringSignal

        signal = ScoringSignal(
            signal_type="test",
            raw_score=0.5,
            weight=0.4,
        )

        # 默认自动计算
        assert signal.weighted_score == 0.2  # 0.5 * 0.4

    def test_create_scoring_signal_minimal(self):
        """测试创建最小字段打分信号"""
        from src.domain.models.scoring_signal import ScoringSignal

        signal = ScoringSignal(
            signal_type="coverage_score",
            raw_score=0.6,
            weight=0.1,
        )

        assert signal.signal_type == "coverage_score"
        assert signal.details == {}  # 默认空字典

    def test_raw_score_range_validation(self):
        """测试原始分数范围验证"""
        from src.domain.models.scoring_signal import ScoringSignal

        # 有效范围 0-1
        signal = ScoringSignal(
            signal_type="test",
            raw_score=0.0,
            weight=0.5,
        )
        assert signal.raw_score == 0.0

        signal = ScoringSignal(
            signal_type="test",
            raw_score=1.0,
            weight=0.5,
        )
        assert signal.raw_score == 1.0

        # 超出范围
        with pytest.raises(ValidationError):
            ScoringSignal(
                signal_type="test",
                raw_score=1.5,
                weight=0.5,
            )

        with pytest.raises(ValidationError):
            ScoringSignal(
                signal_type="test",
                raw_score=-0.1,
                weight=0.5,
            )

    def test_weight_range_validation(self):
        """测试权重范围验证"""
        from src.domain.models.scoring_signal import ScoringSignal

        # 有效范围 0-1
        signal = ScoringSignal(
            signal_type="test",
            raw_score=0.5,
            weight=0.0,
        )
        assert signal.weight == 0.0

        signal = ScoringSignal(
            signal_type="test",
            raw_score=0.5,
            weight=1.0,
        )
        assert signal.weight == 1.0

        # 超出范围
        with pytest.raises(ValidationError):
            ScoringSignal(
                signal_type="test",
                raw_score=0.5,
                weight=1.5,
            )

    def test_missing_required_fields_raises_error(self):
        """测试缺少必填字段抛出错误"""
        from src.domain.models.scoring_signal import ScoringSignal

        with pytest.raises(ValidationError):
            ScoringSignal(
                raw_score=0.5,
                weight=0.25,
            )

        with pytest.raises(ValidationError):
            ScoringSignal(
                signal_type="test",
                weight=0.25,
            )

        with pytest.raises(ValidationError):
            ScoringSignal(
                signal_type="test",
                raw_score=0.5,
            )

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        from src.domain.models.scoring_signal import ScoringSignal

        with pytest.raises(ValidationError):
            ScoringSignal(
                signal_type="test",
                raw_score=0.5,
                weight=0.25,
                extra_field="not_allowed",  # type: ignore
            )


class TestScoringSignalTypes:
    """测试预定义的信号类型常量"""

    def test_predefined_signal_types(self):
        """测试预定义的信号类型常量"""
        from src.domain.models.scoring_signal import SignalType

        assert SignalType.CONTEXT_MATCH == "context_match"
        assert SignalType.SKILL_NAME_MATCH == "skill_name_match"
        assert SignalType.SKILL_DESC_MATCH == "skill_desc_match"
        assert SignalType.SEARCHABLE_MATCH == "searchable_match"
        assert SignalType.COVERAGE_SCORE == "coverage_score"
        assert SignalType.PROFILE_TYPE_BONUS == "profile_type_bonus"
        assert SignalType.DOMAIN_COVERAGE == "domain_coverage"