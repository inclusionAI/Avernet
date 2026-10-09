"""
Tests for StructuredConflictAnalysis Model

G2 Phase B - 结构化冲突分析模型测试
"""

from __future__ import annotations

import pytest

from src.domain.models.stance_signal import StanceSignal
from src.domain.models.structured_conflict_analysis import (
    PairwiseConflict,
    DimensionSummary,
    StructuredConflictAnalysis,
)


class TestPairwiseConflict:
    """PairwiseConflict 模型测试"""

    def _create_stance_signal(self, participant_id: str, position: str) -> StanceSignal:
        """Helper: 创建立场信号"""
        return StanceSignal(
            participant_id=participant_id,
            dimension_id="speed_vs_quality",
            position=position,
            strength=0.6,
            confidence=0.7,
        )

    def test_create_pairwise_conflict(self):
        """测试创建 PairwiseConflict"""
        stance_a = self._create_stance_signal("pm", "axis_a")
        stance_b = self._create_stance_signal("tech", "axis_b")

        conflict = PairwiseConflict(
            participant_a="pm",
            participant_b="tech",
            dimension_id="speed_vs_quality",
            conflict_type="conflict",
            stance_a=stance_a,
            stance_b=stance_b,
            severity="high",
            confidence=0.65,
            evidence=["快速", "质量"],
            rationale="立场对立",
        )

        assert conflict.participant_a == "pm"
        assert conflict.participant_b == "tech"
        assert conflict.conflict_type == "conflict"
        assert conflict.severity == "high"
        assert conflict.confidence == 0.65

    def test_conflict_type_values(self):
        """测试冲突类型值"""
        valid_types = ["conflict", "alignment", "tension", "none"]
        stance_a = self._create_stance_signal("pm", "axis_a")
        stance_b = self._create_stance_signal("tech", "axis_b")

        for conflict_type in valid_types:
            pc = PairwiseConflict(
                participant_a="pm",
                participant_b="tech",
                dimension_id="test",
                conflict_type=conflict_type,
                stance_a=stance_a,
                stance_b=stance_b,
            )
            assert pc.conflict_type == conflict_type

    def test_severity_values(self):
        """测试严重程度值"""
        valid_severities = ["low", "medium", "high", "critical"]
        stance_a = self._create_stance_signal("pm", "axis_a")
        stance_b = self._create_stance_signal("tech", "axis_b")

        for severity in valid_severities:
            pc = PairwiseConflict(
                participant_a="pm",
                participant_b="tech",
                dimension_id="test",
                conflict_type="conflict",
                stance_a=stance_a,
                stance_b=stance_b,
                severity=severity,
            )
            assert pc.severity == severity


class TestDimensionSummary:
    """DimensionSummary 模型测试"""

    def test_create_dimension_summary(self):
        """测试创建 DimensionSummary"""
        summary = DimensionSummary(
            dimension_id="speed_vs_quality",
            dimension_name="速度与质量",
            conflict_count=2,
            alignment_count=1,
            tension_count=1,
            dominant_position="axis_a",
            participants=["pm", "tech", "qa"],
        )

        assert summary.dimension_id == "speed_vs_quality"
        assert summary.dimension_name == "速度与质量"
        assert summary.conflict_count == 2
        assert summary.alignment_count == 1
        assert summary.tension_count == 1
        assert summary.dominant_position == "axis_a"
        assert len(summary.participants) == 3

    def test_dimension_summary_defaults(self):
        """测试 DimensionSummary 默认值"""
        summary = DimensionSummary(
            dimension_id="test",
            dimension_name="测试维度",
        )

        assert summary.conflict_count == 0
        assert summary.alignment_count == 0
        assert summary.tension_count == 0
        assert summary.dominant_position is None
        assert summary.participants == []


class TestStructuredConflictAnalysis:
    """StructuredConflictAnalysis 模型测试"""

    def _create_stance_signal(self, participant_id: str, position: str) -> StanceSignal:
        """Helper: 创建立场信号"""
        return StanceSignal(
            participant_id=participant_id,
            dimension_id="speed_vs_quality",
            position=position,
            strength=0.6,
            confidence=0.7,
        )

    def _create_pairwise_conflict(
        self, p1: str, p2: str, conflict_type: str, severity: str = None
    ) -> PairwiseConflict:
        """Helper: 创建两两冲突"""
        stance_a = self._create_stance_signal(p1, "axis_a")
        stance_b = self._create_stance_signal(p2, "axis_b")

        return PairwiseConflict(
            participant_a=p1,
            participant_b=p2,
            dimension_id="speed_vs_quality",
            conflict_type=conflict_type,
            stance_a=stance_a,
            stance_b=stance_b,
            severity=severity,
            confidence=0.65,
        )

    def test_create_structured_conflict_analysis(self):
        """测试创建 StructuredConflictAnalysis"""
        stance_signals = [
            self._create_stance_signal("pm", "axis_a"),
            self._create_stance_signal("tech", "axis_b"),
        ]
        pairwise = [
            self._create_pairwise_conflict("pm", "tech", "conflict", "high"),
        ]
        summaries = [
            DimensionSummary(
                dimension_id="speed_vs_quality",
                dimension_name="速度与质量",
                conflict_count=1,
                alignment_count=0,
                tension_count=0,
            ),
        ]

        analysis = StructuredConflictAnalysis(
            pairwise_analyses=pairwise,
            dimension_summaries=summaries,
            stance_signals=stance_signals,
            overall_conflict_level="high",
            overall_alignment_level="none",
        )

        assert len(analysis.pairwise_analyses) == 1
        assert len(analysis.dimension_summaries) == 1
        assert len(analysis.stance_signals) == 2
        assert analysis.overall_conflict_level == "high"
        assert analysis.overall_alignment_level == "none"

    def test_default_values(self):
        """测试默认值"""
        analysis = StructuredConflictAnalysis()

        assert analysis.pairwise_analyses == []
        assert analysis.dimension_summaries == []
        assert analysis.stance_signals == []
        assert analysis.overall_conflict_level == "none"
        assert analysis.overall_alignment_level == "none"
        assert analysis.key_conflicts == []
        assert analysis.key_alignments == []
        assert analysis.recommendation is None

    def test_get_conflicts_for_participant(self):
        """测试获取参与者的冲突"""
        pairwise = [
            self._create_pairwise_conflict("pm", "tech", "conflict"),
            self._create_pairwise_conflict("pm", "qa", "conflict"),
            self._create_pairwise_conflict("tech", "qa", "alignment"),
        ]

        analysis = StructuredConflictAnalysis(pairwise_analyses=pairwise)

        pm_conflicts = analysis.get_conflicts_for_participant("pm")
        assert len(pm_conflicts) == 2

        tech_conflicts = analysis.get_conflicts_for_participant("tech")
        assert len(tech_conflicts) == 1

        qa_conflicts = analysis.get_conflicts_for_participant("qa")
        assert len(qa_conflicts) == 1

    def test_get_alignments_for_participant(self):
        """测试获取参与者的对齐"""
        pairwise = [
            self._create_pairwise_conflict("pm", "tech", "conflict"),
            self._create_pairwise_conflict("pm", "qa", "alignment"),
            self._create_pairwise_conflict("tech", "qa", "alignment"),
        ]

        analysis = StructuredConflictAnalysis(pairwise_analyses=pairwise)

        pm_alignments = analysis.get_alignments_for_participant("pm")
        assert len(pm_alignments) == 1

        tech_alignments = analysis.get_alignments_for_participant("tech")
        assert len(tech_alignments) == 1

    def test_get_stance_for_participant(self):
        """测试获取参与者的立场"""
        stance_signals = [
            self._create_stance_signal("pm", "axis_a"),
            self._create_stance_signal("tech", "axis_b"),
        ]

        analysis = StructuredConflictAnalysis(stance_signals=stance_signals)

        pm_stance = analysis.get_stance_for_participant("pm", "speed_vs_quality")
        assert pm_stance is not None
        assert pm_stance.position == "axis_a"

        tech_stance = analysis.get_stance_for_participant("tech", "speed_vs_quality")
        assert tech_stance is not None
        assert tech_stance.position == "axis_b"

        # 不存在的参与者
        none_stance = analysis.get_stance_for_participant("unknown", "speed_vs_quality")
        assert none_stance is None

        # 不存在的维度
        wrong_dim_stance = analysis.get_stance_for_participant("pm", "unknown_dim")
        assert wrong_dim_stance is None

    def test_overall_conflict_level_values(self):
        """测试整体冲突程度值"""
        valid_levels = ["none", "low", "medium", "high", "critical"]

        for level in valid_levels:
            analysis = StructuredConflictAnalysis(overall_conflict_level=level)
            assert analysis.overall_conflict_level == level

    def test_overall_alignment_level_values(self):
        """测试整体对齐程度值"""
        valid_levels = ["none", "low", "medium", "high"]

        for level in valid_levels:
            analysis = StructuredConflictAnalysis(overall_alignment_level=level)
            assert analysis.overall_alignment_level == level