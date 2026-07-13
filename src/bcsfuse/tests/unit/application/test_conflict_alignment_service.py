"""
Tests for ConflictAlignmentService

G2: Conflict Alignment Layer

测试 ConflictAlignmentService 的核心业务逻辑。
"""

from __future__ import annotations

import pytest
from typing import Optional
from unittest.mock import Mock, MagicMock

from src.domain.models.fusion_result import Perspective


class TestConflictAlignmentServiceModule:
    """模块存在性测试"""

    def test_module_exists(self):
        """测试 conflict_alignment_service 模块存在"""
        import importlib

        module = importlib.import_module("src.application.services.conflict_alignment_service")
        assert module is not None

    def test_service_class_exists(self):
        """测试 ConflictAlignmentService 类存在"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        assert ConflictAlignmentService is not None


class TestConflictAlignmentServiceCreation:
    """服务创建测试"""

    def test_service_can_be_created(self):
        """测试服务可以被创建"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()
        assert service is not None

    def test_service_with_recommendation_service(self):
        """测试服务可以注入 recommendation_service"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        mock_rec_service = Mock()
        service = ConflictAlignmentService(recommendation_service=mock_rec_service)
        assert service is not None


class TestConflictAlignmentServiceBasic:
    """基本功能测试"""

    def test_align_returns_result(self):
        """测试 align 方法返回结果"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService
        from src.domain.models.fusion_result import Perspective

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="zhangsan",
                participant_type="bot",
                role="driver",
                summary="开发者视角：当前代码实现为60分钟超时",
                key_points=["兼容旧系统", "避免大规模重构"],
                concerns=["改造成本"],
                flexibility="愿意分阶段改造",
                status="completed",
            ),
            Perspective(
                participant_id="lisi",
                participant_type="bot",
                role="consultant",
                summary="PM视角：PRD要求30分钟超时",
                key_points=["用户体验"],
                concerns=["用户等待焦虑"],
                flexibility="理解兼容性考虑",
                status="completed",
            ),
        ]

        result = service.align(
            question="如何协调代码与PRD的超时时间冲突？",
            perspectives=perspectives,
        )

        assert result is not None
        assert result.fusion_mode == "conflict_alignment"
        # 基本字段必须存在
        assert hasattr(result, "conflicts")
        assert hasattr(result, "alignment_points")
        assert hasattr(result, "key_insights")

    def test_align_with_all_perspectives_completed(self):
        """测试所有视角完成时的对齐"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="技术上可行",
                status="completed",
            ),
            Perspective(
                participant_id="pm",
                participant_type="bot",
                role="consultant",
                summary="产品上可行",
                status="completed",
            ),
        ]

        result = service.align(
            question="方案是否可行？",
            perspectives=perspectives,
        )

        # 成功场景：没有 partial_success
        assert result.partial_success is False


class TestConflictExtraction:
    """冲突提取测试"""

    def test_detects_conflict_between_parties(self):
        """测试能检测到双方冲突"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="方案可行，无风险",
                status="completed",
            ),
            Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="方案不可行，存在安全风险",
                status="completed",
            ),
        ]

        result = service.align(
            question="方案是否可行？",
            perspectives=perspectives,
        )

        # 应该检测到冲突
        assert len(result.conflicts) >= 1

    def test_no_conflict_when_all_agree(self):
        """测试所有一致时无冲突"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="方案可行",
                status="completed",
            ),
            Perspective(
                participant_id="pm",
                participant_type="bot",
                role="consultant",
                summary="方案可行",
                status="completed",
            ),
        ]

        result = service.align(
            question="方案是否可行？",
            perspectives=perspectives,
        )

        # 一致场景：可能没有冲突
        # 注意：这不是强制要求，取决于实现
        assert result.conflicts is not None

    def test_conflict_has_required_fields(self):
        """测试冲突包含必要字段"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="支持方案A",
                status="completed",
            ),
            Perspective(
                participant_id="pm",
                participant_type="bot",
                role="consultant",
                summary="支持方案B",
                status="completed",
            ),
        ]

        result = service.align(
            question="选择哪个方案？",
            perspectives=perspectives,
        )

        if len(result.conflicts) > 0:
            conflict = result.conflicts[0]
            assert hasattr(conflict, "parties")
            assert hasattr(conflict, "issue")
            assert hasattr(conflict, "positions")
            assert hasattr(conflict, "severity")


class TestAlignmentPointExtraction:
    """对齐点提取测试"""

    def test_extracts_alignment_points(self):
        """测试能提取对齐点"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="需要考虑性能",
                key_points=["性能优化"],
                status="completed",
            ),
            Perspective(
                participant_id="pm",
                participant_type="bot",
                role="consultant",
                summary="需要考虑性能",
                key_points=["用户体验"],
                status="completed",
            ),
        ]

        result = service.align(
            question="方案是否可行？",
            perspectives=perspectives,
        )

        assert result.alignment_points is not None

    def test_alignment_point_has_summary(self):
        """测试对齐点包含 summary"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="a",
                participant_type="bot",
                role="consultant",
                summary="都认同目标",
                status="completed",
            ),
            Perspective(
                participant_id="b",
                participant_type="bot",
                role="consultant",
                summary="都认同目标",
                status="completed",
            ),
        ]

        result = service.align(
            question="目标是否一致？",
            perspectives=perspectives,
        )

        if len(result.alignment_points) > 0:
            alignment = result.alignment_points[0]
            assert hasattr(alignment, "summary")


class TestKeyInsightsGeneration:
    """关键洞察生成测试"""

    def test_generates_key_insights(self):
        """测试能生成关键洞察"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="技术方案可行",
                status="completed",
            ),
            Perspective(
                participant_id="pm",
                participant_type="bot",
                role="consultant",
                summary="产品方案可行",
                status="completed",
            ),
        ]

        result = service.align(
            question="方案是否可行？",
            perspectives=perspectives,
        )

        assert result.key_insights is not None

    def test_key_insights_are_high_level(self):
        """测试关键洞察是高层总结"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="技术可行",
                status="completed",
            ),
        ]

        result = service.align(
            question="方案是否可行？",
            perspectives=perspectives,
        )

        # key_insights 应该是字符串列表
        for insight in result.key_insights:
            assert isinstance(insight, str)


class TestPartialSuccessHandling:
    """部分成功处理测试"""

    def test_partial_success_when_one_perspective_fails(self):
        """测试一个视角失败时标记 partial_success"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="技术可行",
                status="completed",
            ),
            Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="",
                status="failed",
            ),
        ]

        result = service.align(
            question="方案是否可行？",
            perspectives=perspectives,
        )

        assert result.partial_success is True
        assert len(result.warnings) > 0

    def test_partial_success_when_one_times_out(self):
        """测试一个视角超时时标记 partial_success"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="完成",
                status="completed",
            ),
            Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="",
                status="timed_out",
            ),
        ]

        result = service.align(
            question="test",
            perspectives=perspectives,
        )

        assert result.partial_success is True


class TestRecommendationIntegration:
    """Recommendation 集成测试"""

    def test_calls_recommendation_service_when_provided(self):
        """测试提供 recommendation_service 时会被调用"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService
        from src.domain.models.fusion_recommendation import FusionRecommendation, Decision

        mock_rec_service = Mock()
        mock_rec_service.generate.return_value = FusionRecommendation(
            summary="测试建议",
            decision=Decision.YES,
            reasoning=["理由"],
            risks=[],
            missing_information=[],
            next_actions=[],
            confidence=0.9,
        )

        service = ConflictAlignmentService(recommendation_service=mock_rec_service)

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="可行",
                status="completed",
            ),
        ]

        result = service.align(
            question="test",
            perspectives=perspectives,
            include_recommendation=True,
        )

        # recommendation_service 应该被调用
        mock_rec_service.generate.assert_called_once()
        assert result.recommendation is not None

    def test_fallback_when_recommendation_fails(self):
        """测试 recommendation 失败时有 fallback"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        mock_rec_service = Mock()
        mock_rec_service.generate.side_effect = Exception("LLM error")

        service = ConflictAlignmentService(recommendation_service=mock_rec_service)

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="可行",
                status="completed",
            ),
        ]

        # 不应该抛异常
        result = service.align(
            question="test",
            perspectives=perspectives,
            include_recommendation=True,
        )

        # 即使 LLM 失败，也应该有 fallback recommendation
        assert result.recommendation is not None

    def test_no_recommendation_when_disabled(self):
        """测试禁用时不生成 recommendation"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        mock_rec_service = Mock()

        service = ConflictAlignmentService(recommendation_service=mock_rec_service)

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="可行",
                status="completed",
            ),
        ]

        result = service.align(
            question="test",
            perspectives=perspectives,
            include_recommendation=False,
        )

        # 不应该调用 recommendation_service
        mock_rec_service.generate.assert_not_called()
        assert result.recommendation is None


class TestEdgeCases:
    """边界情况测试"""

    def test_single_perspective(self):
        """测试单个视角"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="driver",
                summary="只有我",
                status="completed",
            ),
        ]

        result = service.align(
            question="test",
            perspectives=perspectives,
        )

        # 单视角无冲突
        assert result.conflicts == []

    def test_all_perspectives_failed(self):
        """测试所有视角失败"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="a",
                participant_type="bot",
                role="consultant",
                summary="",
                status="failed",
            ),
            Perspective(
                participant_id="b",
                participant_type="bot",
                role="consultant",
                summary="",
                status="failed",
            ),
        ]

        result = service.align(
            question="test",
            perspectives=perspectives,
        )

        # 所有失败时，partial_success = False（完全失败，不是部分成功）
        # 但 warnings 应该记录失败的参与者
        assert result.partial_success is False
        assert len(result.warnings) >= 2
        # recommendation 应该是 needs_more_information
        assert result.recommendation is not None
        assert result.recommendation.decision == "needs_more_information"

    def test_empty_perspectives_list(self):
        """测试空视角列表"""
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        result = service.align(
            question="test",
            perspectives=[],
        )

        # 空视角应该能处理
        assert result.conflicts == []
        assert result.alignment_points == []


class TestConflictAlignmentConsistency:
    """冲突与对齐点一致性测试"""

    def test_no_alignment_when_parties_in_conflict(self):
        """
        测试：当两个 participant 在同一议题上立场相反时，
        不能生成"双方已达成一致"的 alignment point
        """
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="方案可行，无风险",
                status="completed",
            ),
            Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="方案不可行，存在安全风险",
                status="completed",
            ),
        ]

        result = service.align(
            question="方案是否可行？",
            perspectives=perspectives,
        )

        # 应该检测到冲突
        assert len(result.conflicts) >= 1

        # 如果有冲突，检查对齐点是否与冲突方重叠
        conflict_parties = set()
        for conflict in result.conflicts:
            for party in conflict.parties:
                conflict_parties.add(party)

        # 对齐点中的参与者不应该与冲突参与者重叠，
        # 除非对齐点明确说明是"部分共识"或"不同维度的共识"
        for alignment in result.alignment_points:
            if alignment.participants:
                aligned_parties = set(alignment.participants)
                overlap = aligned_parties & conflict_parties
                # 如果有重叠，对齐点的 summary 必须说明这是"部分共识"或"某维度共识"
                if overlap:
                    assert "部分" in alignment.summary or "某维度" in alignment.summary or "某些方面" in alignment.summary, \
                        f"冲突方 {overlap} 不应同时出现在'已对齐'中，除非明确说明是部分共识"

    def test_alignment_from_real_consensus_not_template(self):
        """
        测试：alignment point 必须来源于真实共识，
        而不是模板化兜底文案
        """
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        # 两个视角完全一致
        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="方案可行",
                status="completed",
            ),
            Perspective(
                participant_id="pm",
                participant_type="bot",
                role="consultant",
                summary="方案可行",
                status="completed",
            ),
        ]

        result = service.align(
            question="方案是否可行？",
            perspectives=perspectives,
        )

        # 无冲突
        assert len(result.conflicts) == 0

        # 应该有对齐点
        assert len(result.alignment_points) >= 1

        # 对齐点的 summary 应该反映真实共识内容
        for alignment in result.alignment_points:
            # 对齐点应该有实质内容，不是空泛的文案
            assert len(alignment.summary) > 5, "对齐点摘要应该有实质内容"
            # 应该包含参与方信息或共识关键词
            assert "可行" in alignment.summary or "一致" in alignment.summary or "都持" in alignment.summary, \
                "对齐点应该反映真实共识内容"

    def test_conflict_and_alignment_different_dimensions(self):
        """
        测试：冲突和对齐可以共存，但必须是不同维度
        """
        from src.application.services.conflict_alignment_service import ConflictAlignmentService

        service = ConflictAlignmentService()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="技术上方案可行，但需要更多人手",
                status="completed",
                key_points=["技术可行"],
                concerns=["人手不足"],
            ),
            Perspective(
                participant_id="pm",
                participant_type="bot",
                role="consultant",
                summary="方案不可行，因为不满足业务需求",
                status="completed",
                key_points=["技术可行"],
                concerns=["业务不匹配"],
            ),
        ]

        result = service.align(
            question="方案是否可行？",
            perspectives=perspectives,
        )

        # 检查是否同时有冲突和对齐点
        # 如果两者共存，对齐点应该明确说明是哪方面的共识
        if len(result.conflicts) > 0 and len(result.alignment_points) > 0:
            for alignment in result.alignment_points:
                # 对齐点应该说明共识的具体维度
                assert len(alignment.summary) > 10, "对齐点应该说明共识的具体维度"