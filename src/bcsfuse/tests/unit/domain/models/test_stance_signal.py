"""
Tests for StanceSignal Model

G2 Phase B - 立场信号模型测试
"""

from __future__ import annotations

import pytest

from src.domain.models.stance_signal import StanceSignal


class TestStanceSignalModel:
    """StanceSignal 模型测试"""

    def test_create_stance_signal_basic(self):
        """测试基本的立场信号创建"""
        signal = StanceSignal(
            participant_id="tech_lead",
            dimension_id="speed_vs_quality",
            position="axis_a",
        )

        assert signal.participant_id == "tech_lead"
        assert signal.dimension_id == "speed_vs_quality"
        assert signal.position == "axis_a"
        assert signal.strength == 0.0
        assert signal.confidence == 0.0
        assert signal.evidence == []
        assert signal.rationale is None

    def test_create_stance_signal_full(self):
        """测试完整的立场信号创建"""
        signal = StanceSignal(
            participant_id="pm",
            dimension_id="growth_vs_stability",
            position="axis_b",
            strength=0.8,
            confidence=0.7,
            evidence=["稳定", "可靠", "风险可控"],
            rationale="强调系统稳定性优先",
        )

        assert signal.participant_id == "pm"
        assert signal.dimension_id == "growth_vs_stability"
        assert signal.position == "axis_b"
        assert signal.strength == 0.8
        assert signal.confidence == 0.7
        assert len(signal.evidence) == 3
        assert signal.rationale == "强调系统稳定性优先"

    def test_position_values(self):
        """测试所有立场值的合法性"""
        valid_positions = ["axis_a", "axis_b", "balanced", "neutral", "unknown"]

        for position in valid_positions:
            signal = StanceSignal(
                participant_id="test",
                dimension_id="test_dim",
                position=position,
            )
            assert signal.position == position

    def test_strength_range(self):
        """测试强度范围限制"""
        # 正常范围
        signal = StanceSignal(
            participant_id="test",
            dimension_id="test_dim",
            position="axis_a",
            strength=0.5,
        )
        assert signal.strength == 0.5

        # 边界值
        signal_min = StanceSignal(
            participant_id="test",
            dimension_id="test_dim",
            position="axis_a",
            strength=0.0,
        )
        assert signal_min.strength == 0.0

        signal_max = StanceSignal(
            participant_id="test",
            dimension_id="test_dim",
            position="axis_a",
            strength=1.0,
        )
        assert signal_max.strength == 1.0

        # 超出范围应该抛出异常
        with pytest.raises(Exception):
            StanceSignal(
                participant_id="test",
                dimension_id="test_dim",
                position="axis_a",
                strength=1.5,
            )

    def test_confidence_range(self):
        """测试置信度范围限制"""
        signal = StanceSignal(
            participant_id="test",
            dimension_id="test_dim",
            position="axis_a",
            confidence=0.6,
        )
        assert signal.confidence == 0.6

        # 超出范围应该抛出异常
        with pytest.raises(Exception):
            StanceSignal(
                participant_id="test",
                dimension_id="test_dim",
                position="axis_a",
                confidence=-0.1,
            )


class TestStanceSignalMethods:
    """StanceSignal 方法测试"""

    def test_is_meaningful_true(self):
        """测试有意义立场信号的判定 - 有意义"""
        signal = StanceSignal(
            participant_id="test",
            dimension_id="speed_vs_quality",
            position="axis_a",
            strength=0.6,
            confidence=0.5,
        )
        assert signal.is_meaningful() is True

    def test_is_meaningful_neutral(self):
        """测试有意义立场信号的判定 - neutral"""
        signal = StanceSignal(
            participant_id="test",
            dimension_id="speed_vs_quality",
            position="neutral",
            strength=0.6,
            confidence=0.5,
        )
        assert signal.is_meaningful() is False

    def test_is_meaningful_unknown(self):
        """测试有意义立场信号的判定 - unknown"""
        signal = StanceSignal(
            participant_id="test",
            dimension_id="speed_vs_quality",
            position="unknown",
            strength=0.6,
            confidence=0.5,
        )
        assert signal.is_meaningful() is False

    def test_is_meaningful_low_confidence(self):
        """测试有意义立场信号的判定 - 低置信度"""
        signal = StanceSignal(
            participant_id="test",
            dimension_id="speed_vs_quality",
            position="axis_a",
            strength=0.6,
            confidence=0.3,
        )
        assert signal.is_meaningful() is False

    def test_is_meaningful_low_strength(self):
        """测试有意义立场信号的判定 - 低强度"""
        signal = StanceSignal(
            participant_id="test",
            dimension_id="speed_vs_quality",
            position="axis_a",
            strength=0.2,
            confidence=0.6,
        )
        assert signal.is_meaningful() is False

    def test_is_opposite_to_true(self):
        """测试对立判定 - 真正对立"""
        signal_a = StanceSignal(
            participant_id="pm",
            dimension_id="speed_vs_quality",
            position="axis_a",
        )
        signal_b = StanceSignal(
            participant_id="tech_lead",
            dimension_id="speed_vs_quality",
            position="axis_b",
        )
        assert signal_a.is_opposite_to(signal_b) is True
        assert signal_b.is_opposite_to(signal_a) is True

    def test_is_opposite_to_different_dimension(self):
        """测试对立判定 - 不同维度"""
        signal_a = StanceSignal(
            participant_id="pm",
            dimension_id="speed_vs_quality",
            position="axis_a",
        )
        signal_b = StanceSignal(
            participant_id="tech_lead",
            dimension_id="cost_vs_security",
            position="axis_b",
        )
        assert signal_a.is_opposite_to(signal_b) is False

    def test_is_opposite_to_same_position(self):
        """测试对立判定 - 相同立场"""
        signal_a = StanceSignal(
            participant_id="pm",
            dimension_id="speed_vs_quality",
            position="axis_a",
        )
        signal_b = StanceSignal(
            participant_id="tech_lead",
            dimension_id="speed_vs_quality",
            position="axis_a",
        )
        assert signal_a.is_opposite_to(signal_b) is False

    def test_is_aligned_with_true(self):
        """测试对齐判定 - 真正对齐"""
        signal_a = StanceSignal(
            participant_id="pm",
            dimension_id="speed_vs_quality",
            position="axis_a",
        )
        signal_b = StanceSignal(
            participant_id="tech_lead",
            dimension_id="speed_vs_quality",
            position="axis_a",
        )
        assert signal_a.is_aligned_with(signal_b) is True

    def test_is_aligned_with_balanced(self):
        """测试对齐判定 - balanced 与任何端都算部分对齐"""
        signal_a = StanceSignal(
            participant_id="pm",
            dimension_id="speed_vs_quality",
            position="balanced",
        )
        signal_b = StanceSignal(
            participant_id="tech_lead",
            dimension_id="speed_vs_quality",
            position="axis_a",
        )
        assert signal_a.is_aligned_with(signal_b) is True

    def test_is_aligned_with_different_dimension(self):
        """测试对齐判定 - 不同维度"""
        signal_a = StanceSignal(
            participant_id="pm",
            dimension_id="speed_vs_quality",
            position="axis_a",
        )
        signal_b = StanceSignal(
            participant_id="tech_lead",
            dimension_id="cost_vs_security",
            position="axis_a",
        )
        assert signal_a.is_aligned_with(signal_b) is False