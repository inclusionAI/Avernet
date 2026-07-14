"""
G2三层Fallback架构集成测试

验证G2冲突分析的三层Fallback架构端到端工作流程：
1. Layer 1: LLM深度研判
2. Layer 2: V2结构化分析增强
3. Layer 3: Legacy规则匹配

测试场景：
- 全层可用，优先Layer1
- Layer1失败，Fallback到Layer2
- Layer2失败，Fallback到Layer3
- Feature Flag控制验证
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock

from src.application.services.conflict_alignment_service import (
    ConflictAlignmentService,
    AnalysisResult,
)
from src.application.services.llm_conflict_analyzer import LLMConflictAnalyzer
from src.application.services.llm_stance_extractor import LLMStanceExtractor
from src.domain.services.conflict_dimension_analyzer import ConflictDimensionAnalyzer
from src.domain.models.fusion_result import Perspective
from src.domain.models.llm_conflict_analysis import (
    LLMConflictAnalysis,
    LLMConflict,
    LLMAlignmentPoint,
    LLMConclusion,
    StanceAnalysis,
)
from src.domain.models.structured_conflict_analysis import StructuredConflictAnalysis
from src.domain.models.stance_signal import StanceSignal
from src.infra.observability.g2_metrics import reset_g2_metrics


# ============================================
# Fixtures
# ============================================

@pytest.fixture
def sample_perspectives():
    """测试用视角列表"""
    return [
        Perspective(
            participant_id="product_team",
            participant_type="bot",
            role="consultant",
            summary="建议快速上线抢占市场，市场竞争激烈需要快速响应",
            key_points=["时间窗口有限", "竞品已上线"],
            concerns=["延迟上线会失去市场机会"],
            confidence=0.85,
            status="completed",
        ),
        Perspective(
            participant_id="tech_team",
            participant_type="bot",
            role="consultant",
            summary="建议确保充分的测试和质量保障，避免线上事故",
            key_points=["需要完整测试", "技术方案需要验证"],
            concerns=["快速上线可能导致质量问题"],
            confidence=0.80,
            status="completed",
        ),
    ]


@pytest.fixture
def mock_llm_analyzer():
    """Mock LLM Analyzer"""
    analyzer = Mock(spec=LLMConflictAnalyzer)
    return analyzer


@pytest.fixture
def mock_llm_stance_extractor():
    """Mock LLM Stance Extractor"""
    extractor = Mock(spec=LLMStanceExtractor)
    return extractor


@pytest.fixture
def mock_conflict_analyzer():
    """Mock Conflict Dimension Analyzer"""
    analyzer = Mock(spec=ConflictDimensionAnalyzer)
    return analyzer


@pytest.fixture
def mock_signal_extractor():
    """Mock Signal Extractor"""
    extractor = Mock()
    extractor.extract_from_perspective = Mock(return_value=[])
    return extractor


def create_llm_analysis_result():
    """创建LLM分析结果"""
    return LLMConflictAnalysis(
        stance_analysis=[
            StanceAnalysis(
                participant_id="product_team",
                stance="支持",
                core_demand="快速上线",
                main_concerns=["市场竞争"],
                flexibility="可协商",
                rationale="市场竞争激烈",
            ),
            StanceAnalysis(
                participant_id="tech_team",
                stance="有条件支持",
                core_demand="质量保障",
                main_concerns=["技术风险"],
                flexibility="可协商",
                rationale="需要平衡速度与质量",
            ),
        ],
        conflicts=[
            LLMConflict(
                parties=["product_team", "tech_team"],
                conflict_type="关注点分歧",
                issue="速度vs质量的权衡",
                severity="medium",
                analysis="产品追求速度，技术关注质量",
            ),
        ],
        alignment_points=[
            LLMAlignmentPoint(
                participants=["product_team", "tech_team"],
                point="都希望产品成功上线",
                significance="共同目标是协调基础",
            ),
        ],
        conclusion=LLMConclusion(
            overall_severity="medium",
            go_no_go="conditional_go",
            resolution_strategy="建议采用分级发布策略",
            conditions=["建立快速回滚机制"],
            priority_actions=["协调发布时间表"],
            reasoning="存在分歧但有共识基础",
        ),
    )


def create_v2_analysis_result():
    """创建V2分析结果"""
    return StructuredConflictAnalysis(
        overall_conflict_level="medium",
        overall_alignment_level="medium",
        stance_signals=[
            StanceSignal(
                participant_id="product_team",
                dimension_id="speed_vs_quality",
                position="axis_a",
                strength=0.8,
                confidence=0.85,
                evidence=["快速上线"],
            ),
            StanceSignal(
                participant_id="tech_team",
                dimension_id="speed_vs_quality",
                position="axis_b",
                strength=0.75,
                confidence=0.80,
                evidence=["质量保障"],
            ),
        ],
    )


# ============================================
# Test Cases
# ============================================

class TestThreeLayerFallback:
    """三层Fallback架构测试"""

    def test_layer1_success_returns_llm_result(
        self,
        mock_llm_analyzer,
        mock_llm_stance_extractor,
        mock_conflict_analyzer,
        mock_signal_extractor,
        sample_perspectives,
    ):
        """测试Layer1成功时返回LLM结果"""
        reset_g2_metrics()

        # Given: Layer1返回成功结果
        mock_llm_analyzer.analyze = Mock(return_value=create_llm_analysis_result())

        service = ConflictAlignmentService(
            llm_analyzer=mock_llm_analyzer,
            llm_stance_extractor=mock_llm_stance_extractor,
            conflict_analyzer=mock_conflict_analyzer,
            signal_extractor=mock_signal_extractor,
        )

        # When: 执行分析，所有层都启用
        with patch('src.application.services.conflict_alignment_service.FeatureFlags.is_enabled') as mock_ff:
            mock_ff.side_effect = lambda flag: flag in [
                "ENABLE_G2_LLM_CONFLICT_ANALYSIS",
                "ENABLE_G2_CONFLICT_DIMENSIONS",
            ]
            result = service.align(
                question="产品希望快速上线，技术担忧质量",
                perspectives=sample_perspectives,
            )

        # Then: 应使用Layer1结果
        assert result is not None
        assert len(result.conflicts) == 1
        assert result.conclusion is not None
        assert result.conclusion.overall_severity == "medium"
        assert result.conclusion.go_no_go == "conditional_go"

        # Layer2不应被调用
        mock_conflict_analyzer.analyze.assert_not_called()

    def test_layer1_fails_fallback_to_layer2(
        self,
        mock_llm_analyzer,
        mock_llm_stance_extractor,
        mock_conflict_analyzer,
        mock_signal_extractor,
        sample_perspectives,
    ):
        """测试Layer1失败时Fallback到Layer2"""
        reset_g2_metrics()

        # Given: Layer1失败，Layer2成功
        mock_llm_analyzer.analyze = Mock(return_value=None)
        mock_llm_stance_extractor.extract_all = Mock(return_value={
            "product_team": [StanceSignal(
                participant_id="product_team",
                dimension_id="speed_vs_quality",
                position="axis_a",
                strength=0.8,
                confidence=0.85,
                evidence=[],
            )],
            "tech_team": [StanceSignal(
                participant_id="tech_team",
                dimension_id="speed_vs_quality",
                position="axis_b",
                strength=0.75,
                confidence=0.80,
                evidence=[],
            )],
        })
        mock_conflict_analyzer.analyze = Mock(return_value=create_v2_analysis_result())

        service = ConflictAlignmentService(
            llm_analyzer=mock_llm_analyzer,
            llm_stance_extractor=mock_llm_stance_extractor,
            conflict_analyzer=mock_conflict_analyzer,
            signal_extractor=mock_signal_extractor,
        )

        # When: 执行分析
        with patch('src.application.services.conflict_alignment_service.FeatureFlags.is_enabled') as mock_ff:
            mock_ff.side_effect = lambda flag: flag in [
                "ENABLE_G2_LLM_CONFLICT_ANALYSIS",
                "ENABLE_G2_LLM_STANCE_EXTRACTION",
                "ENABLE_G2_CONFLICT_DIMENSIONS",
            ]
            result = service.align(
                question="产品希望快速上线，技术担忧质量",
                perspectives=sample_perspectives,
            )

        # Then: 应使用Layer2结果
        assert result is not None
        assert result.structured_conflict_analysis is not None

        # Layer2应被调用
        mock_conflict_analyzer.analyze.assert_called_once()

    def test_layer2_fails_fallback_to_legacy(
        self,
        mock_llm_analyzer,
        mock_llm_stance_extractor,
        mock_conflict_analyzer,
        mock_signal_extractor,
        sample_perspectives,
    ):
        """测试Layer2失败时Fallback到Legacy"""
        reset_g2_metrics()

        # Given: Layer1和Layer2都失败
        mock_llm_analyzer.analyze = Mock(return_value=None)
        mock_conflict_analyzer.analyze = Mock(return_value=StructuredConflictAnalysis(
            overall_conflict_level="none",
        ))

        service = ConflictAlignmentService(
            llm_analyzer=mock_llm_analyzer,
            llm_stance_extractor=mock_llm_stance_extractor,
            conflict_analyzer=mock_conflict_analyzer,
            signal_extractor=mock_signal_extractor,
        )

        # When: 执行分析
        with patch('src.application.services.conflict_alignment_service.FeatureFlags.is_enabled') as mock_ff:
            mock_ff.side_effect = lambda flag: flag in [
                "ENABLE_G2_LLM_CONFLICT_ANALYSIS",
                "ENABLE_G2_CONFLICT_DIMENSIONS",
            ]
            result = service.align(
                question="产品希望快速上线，技术担忧质量",
                perspectives=sample_perspectives,
            )

        # Then: 应使用Legacy规则结果（总是有结果）
        assert result is not None
        assert result.conclusion is not None

    def test_layer1_disabled_uses_layer2(
        self,
        mock_llm_analyzer,
        mock_llm_stance_extractor,
        mock_conflict_analyzer,
        mock_signal_extractor,
        sample_perspectives,
    ):
        """测试Layer1禁用时直接使用Layer2"""
        reset_g2_metrics()

        # Given: Layer1禁用，Layer2可用
        # 设置信号提取器返回信号
        mock_signal_extractor.extract_from_perspective = Mock(return_value=[
            StanceSignal(
                participant_id="product_team",
                dimension_id="speed_vs_quality",
                position="axis_a",
                strength=0.8,
                confidence=0.85,
                evidence=[],
            ),
        ])
        mock_conflict_analyzer.analyze = Mock(return_value=create_v2_analysis_result())

        service = ConflictAlignmentService(
            llm_analyzer=mock_llm_analyzer,
            llm_stance_extractor=mock_llm_stance_extractor,
            conflict_analyzer=mock_conflict_analyzer,
            signal_extractor=mock_signal_extractor,
        )

        # When: 执行分析，Layer1禁用
        with patch('src.application.services.conflict_alignment_service.FeatureFlags.is_enabled') as mock_ff:
            # Layer1禁用，Layer2启用
            mock_ff.side_effect = lambda flag: flag == "ENABLE_G2_CONFLICT_DIMENSIONS"
            result = service.align(
                question="产品希望快速上线，技术担忧质量",
                perspectives=sample_perspectives,
            )

        # Then: Layer1不应被调用，Layer2应被调用
        assert result is not None
        mock_llm_analyzer.analyze.assert_not_called()
        mock_conflict_analyzer.analyze.assert_called()

    def test_all_layers_disabled_uses_legacy(
        self,
        mock_llm_analyzer,
        mock_llm_stance_extractor,
        mock_conflict_analyzer,
        mock_signal_extractor,
        sample_perspectives,
    ):
        """测试所有高级层禁用时使用Legacy"""
        reset_g2_metrics()

        service = ConflictAlignmentService(
            llm_analyzer=mock_llm_analyzer,
            llm_stance_extractor=mock_llm_stance_extractor,
            conflict_analyzer=mock_conflict_analyzer,
            signal_extractor=mock_signal_extractor,
        )

        # When: 所有高级层都禁用
        with patch('src.application.services.conflict_alignment_service.FeatureFlags.is_enabled', return_value=False):
            result = service.align(
                question="产品希望快速上线，技术担忧质量",
                perspectives=sample_perspectives,
            )

        # Then: Legacy规则仍然工作
        assert result is not None
        assert result.conclusion is not None


class TestAnalysisResultFormat:
    """分析结果格式测试"""

    def test_llm_result_conversion(
        self,
        mock_llm_analyzer,
        mock_llm_stance_extractor,
        mock_conflict_analyzer,
        mock_signal_extractor,
        sample_perspectives,
    ):
        """测试LLM结果转换格式正确"""
        reset_g2_metrics()

        mock_llm_analyzer.analyze = Mock(return_value=create_llm_analysis_result())

        service = ConflictAlignmentService(
            llm_analyzer=mock_llm_analyzer,
            llm_stance_extractor=mock_llm_stance_extractor,
            conflict_analyzer=mock_conflict_analyzer,
            signal_extractor=mock_signal_extractor,
        )

        with patch('src.application.services.conflict_alignment_service.FeatureFlags.is_enabled') as mock_ff:
            mock_ff.side_effect = lambda flag: flag in [
                "ENABLE_G2_LLM_CONFLICT_ANALYSIS",
                "ENABLE_G2_CONFLICT_DIMENSIONS",
            ]
            result = service.align(
                question="测试问题",
                perspectives=sample_perspectives,
            )

        # 验证格式
        assert hasattr(result, 'conflicts')
        assert hasattr(result, 'alignment_points')
        assert hasattr(result, 'conclusion')
        assert hasattr(result, 'key_insights')

        # 验证冲突格式
        if result.conflicts:
            assert hasattr(result.conflicts[0], 'parties')
            assert hasattr(result.conflicts[0], 'issue')
            assert hasattr(result.conflicts[0], 'severity')

        # 验证结论格式
        if result.conclusion:
            assert hasattr(result.conclusion, 'overall_severity')
            assert hasattr(result.conclusion, 'go_no_go')
            assert result.conclusion.go_no_go in ["go", "conditional_go", "need_discussion", "no_go"]


class TestMetricsRecording:
    """指标记录测试"""

    def test_metrics_recorded_on_layer1_success(
        self,
        mock_llm_analyzer,
        sample_perspectives,
    ):
        """测试Layer1成功时记录指标"""
        from src.infra.observability.g2_metrics import get_g2_metrics

        reset_g2_metrics()

        mock_llm_analyzer.analyze = Mock(return_value=create_llm_analysis_result())

        service = ConflictAlignmentService(
            llm_analyzer=mock_llm_analyzer,
        )

        with patch('src.application.services.conflict_alignment_service.FeatureFlags.is_enabled') as mock_ff:
            mock_ff.side_effect = lambda flag: flag in [
                "ENABLE_G2_LLM_CONFLICT_ANALYSIS",
                "ENABLE_G2_CONFLICT_DIMENSIONS",
            ]
            service.align(
                question="测试问题",
                perspectives=sample_perspectives,
            )

        metrics = get_g2_metrics()
        summary = metrics.get_summary()

        # 验证Layer1被记录
        assert summary["layer_stats"]["llm"]["total_calls"] == 1
        assert summary["layer_stats"]["llm"]["success_count"] == 1

        # 验证最终结果被记录
        assert summary["total_analyses"] == 1
        assert summary["final_source_distribution"]["llm"] == 1


class TestEdgeCases:
    """边界情况测试"""

    def test_empty_perspectives(
        self,
        mock_llm_analyzer,
        mock_conflict_analyzer,
    ):
        """测试空视角列表"""
        reset_g2_metrics()

        service = ConflictAlignmentService(
            llm_analyzer=mock_llm_analyzer,
            conflict_analyzer=mock_conflict_analyzer,
        )

        with patch('src.application.services.conflict_alignment_service.FeatureFlags.is_enabled', return_value=False):
            result = service.align(
                question="测试问题",
                perspectives=[],
            )

        # 应返回结果（即使没有冲突）
        assert result is not None
        assert len(result.conflicts) == 0

    def test_single_perspective(
        self,
        mock_llm_analyzer,
        mock_conflict_analyzer,
    ):
        """测试单个视角"""
        reset_g2_metrics()

        single_perspective = [
            Perspective(
                participant_id="only_team",
                participant_type="bot",
                role="consultant",
                summary="单方观点",
                key_points=["point1"],
                concerns=["concern1"],
                confidence=0.8,
                status="completed",
            )
        ]

        service = ConflictAlignmentService(
            llm_analyzer=mock_llm_analyzer,
            conflict_analyzer=mock_conflict_analyzer,
        )

        with patch('src.application.services.conflict_alignment_service.FeatureFlags.is_enabled', return_value=False):
            result = service.align(
                question="测试问题",
                perspectives=single_perspective,
            )

        # 单个视角不应有冲突
        assert result is not None
        assert len(result.conflicts) == 0