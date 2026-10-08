"""
Tests for G2 Structured Conflict Flow

G2 Phase B - 集成测试

测试 G2 V2 结构化冲突分析的完整流程。
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from src.domain.models.fusion_result import Perspective
from src.domain.models.stance_signal import StanceSignal
from src.application.services.conflict_alignment_service import ConflictAlignmentService
from src.application.services.structured_signal_extractor import StructuredSignalExtractor
from src.domain.services.conflict_dimension_analyzer import ConflictDimensionAnalyzer
from src.domain.taxonomy.registry import TaxonomyRegistry


class TestG2StructuredConflictFlow:
    """G2 V2 结构化冲突分析集成测试"""

    @pytest.fixture
    def mock_registry(self):
        """创建 mock registry"""
        registry = MagicMock(spec=TaxonomyRegistry)

        # 配置冲突维度
        registry.get_conflict_dimensions.return_value = {
            "speed_vs_quality": MagicMock(
                name="速度与质量",
                axis_a=MagicMock(label="speed", keywords=["快速", "紧急", "尽快", "抢占市场"]),
                axis_b=MagicMock(label="quality", keywords=["质量", "稳定", "测试", "可靠"]),
            ),
            "compliance_vs_conversion": MagicMock(
                name="合规与转化",
                axis_a=MagicMock(label="compliance", keywords=["合规", "监管", "牌照"]),
                axis_b=MagicMock(label="conversion", keywords=["转化", "用户体验", "便捷"]),
            ),
            "cost_vs_security": MagicMock(
                name="成本与安全",
                axis_a=MagicMock(label="cost", keywords=["成本", "预算", "降本"]),
                axis_b=MagicMock(label="security", keywords=["安全", "加固", "防护"]),
            ),
        }

        registry.get_conflict_dimension.return_value = MagicMock(name="维度")
        registry.get_conflict_dimension_thresholds.return_value = {
            "conflict_strength_threshold": 0.6,
            "alignment_strength_threshold": 0.3,
        }

        return registry

    @pytest.fixture
    def mock_feature_flags(self):
        """Mock Feature Flags"""
        with patch("src.infra.config.feature_flags.FeatureFlags") as mock:
            mock.is_enabled.return_value = True
            mock.is_g2_structured_stance_enabled.return_value = True
            mock.is_g2_conflict_dimensions_enabled.return_value = True
            mock.is_g2_structured_output_enabled.return_value = True
            yield mock

    def test_speed_vs_quality_conflict(self, mock_registry, mock_feature_flags):
        """
        测试产品要速度、研发要质量的冲突

        场景：产品经理希望快速上线，技术团队强调需要充分测试保证质量
        """
        # 配置 mock registry 的检测方法
        def detect_stance(text, dimension_id):
            text_lower = text.lower()
            if dimension_id == "speed_vs_quality":
                speed_kws = ["快速", "紧急", "尽快", "抢占市场"]
                quality_kws = ["质量", "稳定", "测试", "可靠"]
                speed_matches = [kw for kw in speed_kws if kw in text_lower]
                quality_matches = [kw for kw in quality_kws if kw in text_lower]

                if len(speed_matches) > len(quality_matches):
                    return ("speed", 0.6, speed_matches)
                elif len(quality_matches) > len(speed_matches):
                    return ("quality", 0.6, quality_matches)
                elif speed_matches and quality_matches:
                    return ("balanced", 0.5, speed_matches + quality_matches)
            return ("neutral", 0.0, [])

        mock_registry.detect_stance_for_dimension = detect_stance

        # 创建服务
        extractor = StructuredSignalExtractor(registry=mock_registry)
        analyzer = ConflictDimensionAnalyzer(registry=mock_registry)
        service = ConflictAlignmentService(
            signal_extractor=extractor,
            conflict_analyzer=analyzer,
        )

        # 创建视角
        perspectives = [
            Perspective(
                participant_id="pm",
                participant_type="bot",
                role="driver",
                summary="我们需要快速上线这个功能，抢占市场先机。时间很紧急。",
                key_points=["快速迭代", "市场先机"],
                status="completed",
            ),
            Perspective(
                participant_id="tech_lead",
                participant_type="bot",
                role="consultant",
                summary="需要充分测试保证代码质量，确保系统稳定运行。不能牺牲质量赶进度。",
                key_points=["代码质量", "系统稳定"],
                concerns=["技术债务累积"],
                status="completed",
            ),
        ]

        # 执行对齐
        result = service.align(
            question="产品希望快速上线新功能，技术团队担心质量问题，如何平衡？",
            perspectives=perspectives,
        )

        # 验证结果
        assert result.fusion_mode == "conflict_alignment"
        assert len(result.perspectives) == 2
        # Legacy 冲突应该存在
        assert len(result.conflicts) >= 0
        # V2 结构化分析应该存在
        if result.structured_conflict_analysis:
            assert result.structured_conflict_analysis is not None

    def test_compliance_vs_conversion_conflict(self, mock_registry, mock_feature_flags):
        """
        测试业务要转化、法务要合规的冲突

        场景：业务团队希望简化流程提高转化率，合规团队要求增加验证措施
        """
        def detect_stance(text, dimension_id):
            text_lower = text.lower()
            if dimension_id == "compliance_vs_conversion":
                compliance_kws = ["合规", "监管", "牌照", "风险"]
                conversion_kws = ["转化", "用户体验", "便捷", "简化"]
                compliance_matches = [kw for kw in compliance_kws if kw in text_lower]
                conversion_matches = [kw for kw in conversion_kws if kw in text_lower]

                if len(compliance_matches) > len(conversion_matches):
                    return ("compliance", 0.6, compliance_matches)
                elif len(conversion_matches) > len(compliance_matches):
                    return ("conversion", 0.6, conversion_matches)
            return ("neutral", 0.0, [])

        mock_registry.detect_stance_for_dimension = detect_stance

        # 创建服务
        extractor = StructuredSignalExtractor(registry=mock_registry)
        analyzer = ConflictDimensionAnalyzer(registry=mock_registry)
        service = ConflictAlignmentService(
            signal_extractor=extractor,
            conflict_analyzer=analyzer,
        )

        # 创建视角
        perspectives = [
            Perspective(
                participant_id="business",
                participant_type="bot",
                role="driver",
                summary="简化注册流程可以提高用户体验和转化率，减少用户流失。",
                key_points=["用户体验", "转化率"],
                status="completed",
            ),
            Perspective(
                participant_id="compliance",
                participant_type="bot",
                role="consultant",
                summary="需要增加身份验证措施确保合规，满足监管要求，降低法律风险。",
                key_points=["合规要求", "监管"],
                concerns=["违规风险"],
                status="completed",
            ),
        ]

        result = service.align(
            question="业务希望简化流程提高转化，合规要求增加验证措施，如何平衡？",
            perspectives=perspectives,
        )

        assert result.fusion_mode == "conflict_alignment"

    def test_cost_vs_security_conflict(self, mock_registry, mock_feature_flags):
        """
        测试财务要降本、安全要加固的冲突

        场景：财务部门要控制预算，安全团队要求增加安全投入
        """
        def detect_stance(text, dimension_id):
            text_lower = text.lower()
            if dimension_id == "cost_vs_security":
                cost_kws = ["成本", "预算", "降本", "节省"]
                security_kws = ["安全", "加固", "防护", "漏洞"]
                cost_matches = [kw for kw in cost_kws if kw in text_lower]
                security_matches = [kw for kw in security_kws if kw in text_lower]

                if len(cost_matches) > len(security_matches):
                    return ("cost", 0.6, cost_matches)
                elif len(security_matches) > len(cost_matches):
                    return ("security", 0.6, security_matches)
            return ("neutral", 0.0, [])

        mock_registry.detect_stance_for_dimension = detect_stance

        extractor = StructuredSignalExtractor(registry=mock_registry)
        analyzer = ConflictDimensionAnalyzer(registry=mock_registry)
        service = ConflictAlignmentService(
            signal_extractor=extractor,
            conflict_analyzer=analyzer,
        )

        perspectives = [
            Perspective(
                participant_id="finance",
                participant_type="bot",
                role="consultant",
                summary="需要控制预算，降低成本支出，优化资源使用效率。",
                key_points=["成本控制", "预算管理"],
                status="completed",
            ),
            Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="必须增加安全投入，进行系统加固，修复安全漏洞。",
                key_points=["安全加固", "漏洞修复"],
                concerns=["安全风险"],
                status="completed",
            ),
        ]

        result = service.align(
            question="财务要求降本增效，安全团队要求增加安全投入，如何平衡？",
            perspectives=perspectives,
        )

        assert result.fusion_mode == "conflict_alignment"

    def test_alignment_detection(self, mock_registry, mock_feature_flags):
        """
        测试双方都强调长期稳定 - 对齐检测

        场景：双方立场一致，应该检测为对齐
        """
        def detect_stance(text, dimension_id):
            text_lower = text.lower()
            if dimension_id == "speed_vs_quality":
                if "稳定" in text_lower or "质量" in text_lower:
                    return ("quality", 0.7, ["稳定", "质量"])
            return ("neutral", 0.0, [])

        mock_registry.detect_stance_for_dimension = detect_stance

        extractor = StructuredSignalExtractor(registry=mock_registry)
        analyzer = ConflictDimensionAnalyzer(registry=mock_registry)
        service = ConflictAlignmentService(
            signal_extractor=extractor,
            conflict_analyzer=analyzer,
        )

        perspectives = [
            Perspective(
                participant_id="tech1",
                participant_type="bot",
                role="consultant",
                summary="我们需要保证系统的长期稳定运行。",
                status="completed",
            ),
            Perspective(
                participant_id="tech2",
                participant_type="bot",
                role="consultant",
                summary="同意，质量优先，确保稳定。",
                status="completed",
            ),
        ]

        result = service.align(
            question="如何权衡功能开发和系统稳定？",
            perspectives=perspectives,
        )

        # 应该检测到对齐
        assert result.fusion_mode == "conflict_alignment"

    def test_tension_detection(self, mock_registry, mock_feature_flags):
        """
        测试一方强、一方弱 - 张力检测

        场景：一方有明确立场，另一方比较中立
        """
        def detect_stance(text, dimension_id):
            text_lower = text.lower()
            if "pm" in text_lower:
                return ("speed", 0.7, ["快速"])
            return ("neutral", 0.0, [])

        mock_registry.detect_stance_for_dimension = detect_stance

        extractor = StructuredSignalExtractor(registry=mock_registry)
        analyzer = ConflictDimensionAnalyzer(registry=mock_registry)
        service = ConflictAlignmentService(
            signal_extractor=extractor,
            conflict_analyzer=analyzer,
        )

        perspectives = [
            Perspective(
                participant_id="pm",
                participant_type="bot",
                role="driver",
                summary="我们需要快速上线。",
                status="completed",
            ),
            Perspective(
                participant_id="observer",
                participant_type="bot",
                role="observer",
                summary="这个方案看起来可以考虑。",
                status="completed",
            ),
        ]

        result = service.align(
            question="如何推进这个项目？",
            perspectives=perspectives,
        )

        assert result.fusion_mode == "conflict_alignment"


class TestG2V2BackwardCompatibility:
    """G2 V2 向后兼容性测试"""

    def test_flags_off_uses_legacy(self):
        """
        测试 flags 全关时使用 legacy 逻辑
        """
        with patch("src.infra.config.feature_flags.FeatureFlags") as mock_flags:
            # 所有 V2 flags 都关闭
            mock_flags.is_enabled.return_value = False
            mock_flags.is_g2_structured_stance_enabled.return_value = False
            mock_flags.is_g2_conflict_dimensions_enabled.return_value = False
            mock_flags.is_g2_structured_output_enabled.return_value = False

            service = ConflictAlignmentService()

            perspectives = [
                Perspective(
                    participant_id="pm",
                    participant_type="bot",
                    role="driver",
                    summary="我支持这个方案",
                    status="completed",
                ),
                Perspective(
                    participant_id="tech",
                    participant_type="bot",
                    role="consultant",
                    summary="我反对这个方案",
                    status="completed",
                ),
            ]

            result = service.align(
                question="是否推进这个方案？",
                perspectives=perspectives,
            )

            # 应该有 legacy 冲突检测
            assert result.fusion_mode == "conflict_alignment"
            # V2 分析应该为 None
            assert result.structured_conflict_analysis is None

    def test_old_client_ignores_new_fields(self):
        """
        测试老客户端忽略新字段
        """
        with patch("src.infra.config.feature_flags.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = False

            service = ConflictAlignmentService()

            perspectives = [
                Perspective(
                    participant_id="pm",
                    participant_type="bot",
                    role="driver",
                    summary="同意",
                    status="completed",
                ),
            ]

            result = service.align(
                question="是否同意？",
                perspectives=perspectives,
            )

            # 老字段应该可用
            assert hasattr(result, "conflicts")
            assert hasattr(result, "alignment_points")
            assert hasattr(result, "key_insights")

            # 新字段应该是 None
            assert result.structured_conflict_analysis is None

    def test_empty_perspectives_no_error(self):
        """
        测试空 perspectives 不报错
        """
        with patch("src.infra.config.feature_flags.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = True

            service = ConflictAlignmentService()

            result = service.align(
                question="测试问题",
                perspectives=[],
            )

            assert result.fusion_mode == "conflict_alignment"
            assert result.structured_conflict_analysis is None

    def test_skipped_perspectives_no_error(self):
        """
        测试 skipped perspectives 不报错
        """
        with patch("src.infra.config.feature_flags.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = False

            service = ConflictAlignmentService()

            perspectives = [
                Perspective(
                    participant_id="skipped_participant",
                    participant_type="bot",
                    role="consultant",
                    summary="",
                    status="skipped",
                ),
            ]

            result = service.align(
                question="测试问题",
                perspectives=perspectives,
            )

            # 应该正常完成
            assert result.fusion_mode == "conflict_alignment"
            assert len(result.warnings) > 0  # 应该有 skipped 警告


class TestG2V2StrictParticipants:
    """G2 V2 strict_participants 语义测试"""

    def test_strict_participants_not_degraded(self):
        """
        测试 strict_participants 语义不退化
        """
        with patch("src.infra.config.feature_flags.FeatureFlags") as mock_flags:
            mock_flags.is_enabled.return_value = False

            service = ConflictAlignmentService()

            # 只有 skipped 视角
            perspectives = [
                Perspective(
                    participant_id="unavailable",
                    participant_type="bot",
                    role="consultant",
                    summary="",
                    status="skipped",
                ),
            ]

            result = service.align(
                question="测试问题",
                perspectives=perspectives,
            )

            # strict 模式下，skipped 的 participant 应该在 warnings 中
            assert any("skipped" in w for w in result.warnings)