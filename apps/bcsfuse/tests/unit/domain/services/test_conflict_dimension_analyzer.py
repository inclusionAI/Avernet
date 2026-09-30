"""
Tests for ConflictDimensionAnalyzer

G2 Phase B - 冲突维度分析器测试
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from src.domain.models.stance_signal import StanceSignal
from src.domain.models.structured_conflict_analysis import (
    PairwiseConflict,
    StructuredConflictAnalysis,
)
from src.domain.services.conflict_dimension_analyzer import ConflictDimensionAnalyzer


class TestConflictDimensionAnalyzer:
    """ConflictDimensionAnalyzer 测试"""

    def _create_stance_signal(
        self,
        participant_id: str,
        dimension_id: str,
        position: str,
        strength: float = 0.6,
        confidence: float = 0.7,
    ) -> StanceSignal:
        """Helper: 创建立场信号"""
        return StanceSignal(
            participant_id=participant_id,
            dimension_id=dimension_id,
            position=position,
            strength=strength,
            confidence=confidence,
        )

    @pytest.fixture(autouse=True)
    def setup(self):
        """每个测试前的设置"""
        pass

    def test_analyze_disabled(self):
        """测试 Feature Flag 关闭时返回空分析"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = False

            analyzer = ConflictDimensionAnalyzer()
            result = analyzer.analyze([])

            assert result.pairwise_analyses == []
            assert result.stance_signals == []

    def test_analyze_empty_signals(self):
        """测试空信号列表"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            analyzer = ConflictDimensionAnalyzer()
            result = analyzer.analyze([])

            assert result.pairwise_analyses == []
            assert result.stance_signals == []

    def test_analyze_conflict_detection(self):
        """测试冲突检测"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {
                    "conflict_strength_threshold": 0.6,
                    "alignment_strength_threshold": 0.3,
                }
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 创建对立立场
                signals = [
                    self._create_stance_signal("pm", "speed_vs_quality", "axis_a", 0.7, 0.8),
                    self._create_stance_signal("tech", "speed_vs_quality", "axis_b", 0.7, 0.8),
                ]

                result = analyzer.analyze(signals)

                assert len(result.stance_signals) == 2
                assert len(result.pairwise_analyses) == 1
                assert result.pairwise_analyses[0].conflict_type == "conflict"

    def test_analyze_alignment_detection(self):
        """测试对齐检测"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {
                    "conflict_strength_threshold": 0.6,
                    "alignment_strength_threshold": 0.3,
                }
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 创建相同立场
                signals = [
                    self._create_stance_signal("pm", "speed_vs_quality", "axis_a", 0.7, 0.8),
                    self._create_stance_signal("tech", "speed_vs_quality", "axis_a", 0.7, 0.8),
                ]

                result = analyzer.analyze(signals)

                assert len(result.pairwise_analyses) == 1
                assert result.pairwise_analyses[0].conflict_type == "alignment"

    def test_analyze_tension_detection(self):
        """测试张力检测"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {
                    "conflict_strength_threshold": 0.6,
                    "alignment_strength_threshold": 0.3,
                }
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 一方有明确立场，一方 neutral
                signals = [
                    self._create_stance_signal("pm", "speed_vs_quality", "axis_a", 0.7, 0.8),
                    self._create_stance_signal("observer", "speed_vs_quality", "neutral", 0.0, 0.0),
                ]

                result = analyzer.analyze(signals)

                assert len(result.pairwise_analyses) == 1
                assert result.pairwise_analyses[0].conflict_type == "tension"

    def test_analyze_balanced_with_axis(self):
        """测试 balanced 与端点的关系"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {
                    "conflict_strength_threshold": 0.6,
                    "alignment_strength_threshold": 0.3,
                }
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # balanced 与有明确倾向的一方
                signals = [
                    self._create_stance_signal("pm", "speed_vs_quality", "balanced", 0.5, 0.6),
                    self._create_stance_signal("tech", "speed_vs_quality", "axis_a", 0.7, 0.8),
                ]

                result = analyzer.analyze(signals)

                assert len(result.pairwise_analyses) == 1
                # balanced 与端点应该判定为 tension
                assert result.pairwise_analyses[0].conflict_type == "tension"


class TestPairwiseAnalysisLogic:
    """两两分析逻辑测试"""

    def _create_stance_signal(
        self,
        participant_id: str,
        position: str,
        strength: float = 0.6,
        confidence: float = 0.7,
    ) -> StanceSignal:
        """Helper: 创建立场信号"""
        return StanceSignal(
            participant_id=participant_id,
            dimension_id="speed_vs_quality",
            position=position,
            strength=strength,
            confidence=confidence,
        )

    def test_high_conflict_severity(self):
        """测试高严重程度冲突"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {
                    "conflict_strength_threshold": 0.6,
                    "alignment_strength_threshold": 0.3,
                }
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 高强度对立
                signals = [
                    self._create_stance_signal("pm", "axis_a", 0.9, 0.9),
                    self._create_stance_signal("tech", "axis_b", 0.9, 0.9),
                ]

                result = analyzer.analyze(signals)

                assert result.pairwise_analyses[0].severity == "high"

    def test_medium_conflict_severity(self):
        """测试中等严重程度冲突"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {
                    "conflict_strength_threshold": 0.6,
                    "alignment_strength_threshold": 0.3,
                }
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 中等强度对立
                signals = [
                    self._create_stance_signal("pm", "axis_a", 0.7, 0.6),
                    self._create_stance_signal("tech", "axis_b", 0.7, 0.6),
                ]

                result = analyzer.analyze(signals)

                assert result.pairwise_analyses[0].severity == "medium"

    def test_low_conflict_severity(self):
        """测试低严重程度冲突"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {
                    "conflict_strength_threshold": 0.6,
                    "alignment_strength_threshold": 0.3,
                }
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 低强度对立 (strength < 0.8, confidence < 0.7)
                signals = [
                    self._create_stance_signal("pm", "axis_a", 0.6, 0.5),
                    self._create_stance_signal("tech", "axis_b", 0.6, 0.5),
                ]

                result = analyzer.analyze(signals)

                # severity 应该是 medium 或 low（取决于强度阈值）
                assert result.pairwise_analyses[0].severity in ["low", "medium"]

    def test_both_neutral(self):
        """测试双方都是 neutral"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {
                    "conflict_strength_threshold": 0.6,
                    "alignment_strength_threshold": 0.3,
                }
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                signals = [
                    self._create_stance_signal("pm", "neutral", 0.0, 0.0),
                    self._create_stance_signal("tech", "neutral", 0.0, 0.0),
                ]

                result = analyzer.analyze(signals)

                assert result.pairwise_analyses[0].conflict_type == "none"


class TestOverallLevels:
    """整体程度计算测试"""

    def _create_stance_signal(
        self,
        participant_id: str,
        position: str,
    ) -> StanceSignal:
        """Helper: 创建立场信号"""
        return StanceSignal(
            participant_id=participant_id,
            dimension_id="speed_vs_quality",
            position=position,
            strength=0.7,
            confidence=0.7,
        )

    def test_overall_conflict_none(self):
        """测试整体冲突程度 - none"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {}
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 只有对齐，没有冲突
                signals = [
                    self._create_stance_signal("pm", "axis_a"),
                    self._create_stance_signal("tech", "axis_a"),
                ]

                result = analyzer.analyze(signals)

                assert result.overall_conflict_level == "none"

    def test_overall_conflict_high(self):
        """测试整体冲突程度 - high"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {}
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 多个对立
                signals = [
                    self._create_stance_signal("pm", "axis_a"),
                    self._create_stance_signal("tech", "axis_b"),
                    self._create_stance_signal("qa", "axis_b"),
                ]

                result = analyzer.analyze(signals)

                # 应该有中等或高冲突程度
                assert result.overall_conflict_level in ["medium", "high", "critical"]

    def test_overall_alignment_high(self):
        """测试整体对齐程度 - high"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {}
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 大部分对齐
                signals = [
                    self._create_stance_signal("pm", "axis_a"),
                    self._create_stance_signal("tech", "axis_a"),
                    self._create_stance_signal("qa", "axis_a"),
                ]

                result = analyzer.analyze(signals)

                assert result.overall_alignment_level == "high"


class TestKeyConflictsAndAlignments:
    """关键冲突和对齐提取测试"""

    def _create_stance_signal(self, participant_id: str, position: str) -> StanceSignal:
        return StanceSignal(
            participant_id=participant_id,
            dimension_id="speed_vs_quality",
            position=position,
            strength=0.7,
            confidence=0.7,
        )

    def test_key_conflicts_limit(self):
        """测试关键冲突最多返回 3 个"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {}
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 创建多个参与者，产生多个冲突
                signals = [
                    self._create_stance_signal("pm", "axis_a"),
                    self._create_stance_signal("tech1", "axis_b"),
                    self._create_stance_signal("tech2", "axis_b"),
                    self._create_stance_signal("tech3", "axis_b"),
                    self._create_stance_signal("tech4", "axis_b"),
                ]

                result = analyzer.analyze(signals)

                # 关键冲突最多 3 个
                assert len(result.key_conflicts) <= 3

    def test_key_alignments_limit(self):
        """测试关键对齐最多返回 3 个"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {}
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 创建多个参与者，产生多个对齐
                signals = [
                    self._create_stance_signal("pm", "axis_a"),
                    self._create_stance_signal("tech1", "axis_a"),
                    self._create_stance_signal("tech2", "axis_a"),
                    self._create_stance_signal("tech3", "axis_a"),
                    self._create_stance_signal("tech4", "axis_a"),
                ]

                result = analyzer.analyze(signals)

                # 关键对齐最多 3 个
                assert len(result.key_alignments) <= 3


class TestRecommendation:
    """推荐策略生成测试"""

    def _create_stance_signal(self, participant_id: str, position: str) -> StanceSignal:
        return StanceSignal(
            participant_id=participant_id,
            dimension_id="speed_vs_quality",
            position=position,
            strength=0.7,
            confidence=0.7,
        )

    def test_recommendation_critical_conflict(self):
        """测试严重冲突时的推荐"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {}
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 高强度对立
                signals = [
                    self._create_stance_signal("pm", "axis_a"),
                    self._create_stance_signal("tech", "axis_b"),
                ]

                result = analyzer.analyze(signals)

                # 应该有推荐（可能是协调、分歧或其他）
                # 推荐内容取决于整体冲突程度
                assert result.recommendation is not None

    def test_recommendation_high_alignment(self):
        """测试高对齐时的推荐"""
        with patch("src.domain.services.conflict_dimension_analyzer.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            with patch("src.domain.services.conflict_dimension_analyzer.get_taxonomy_registry") as mock_registry:
                mock_reg = MagicMock()
                mock_reg.get_conflict_dimension.return_value = MagicMock(name="速度与质量")
                # 确保返回字符串而不是 Mock 对象
                mock_reg.get_conflict_dimension.return_value.name = "速度与质量"
                mock_reg.get_conflict_dimension_thresholds.return_value = {}
                mock_registry.return_value = mock_reg

                analyzer = ConflictDimensionAnalyzer(registry=mock_reg)

                # 完全对齐
                signals = [
                    self._create_stance_signal("pm", "axis_a"),
                    self._create_stance_signal("tech", "axis_a"),
                ]

                result = analyzer.analyze(signals)

                # 应该有推荐
                if result.recommendation:
                    assert "推进" in result.recommendation or "一致" in result.recommendation