"""
Tests for FusionResult domain models

G1: Fusion Entry Layer

测试 FusionResult, Perspective, Recommendation, FusionTiming 的模型定义。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestFusionResultModule:
    """模块存在性测试"""

    def test_module_exists(self):
        """测试 fusion_result 模块存在"""
        import importlib

        module = importlib.import_module("src.domain.models.fusion_result")
        assert module is not None

    def test_fusion_result_class_exists(self):
        """测试 FusionResult 类存在"""
        from src.domain.models.fusion_result import FusionResult

        assert FusionResult is not None


class TestPerspective:
    """Perspective 测试"""

    def test_create_minimal_perspective(self):
        """测试创建最小 Perspective"""
        from src.domain.models.fusion_result import Perspective

        perspective = Perspective(
            participant_id="dba",
            participant_type="bot",
            role="consultant",
            summary="从数据库角度整体可行。",
            status="completed",
        )

        assert perspective.participant_id == "dba"
        assert perspective.participant_type == "bot"
        assert perspective.role == "consultant"
        assert perspective.summary == "从数据库角度整体可行。"
        assert perspective.confidence is None
        assert perspective.evidence == []
        assert perspective.status == "completed"

    def test_create_full_perspective(self):
        """测试创建完整 Perspective"""
        from src.domain.models.fusion_result import Perspective

        perspective = Perspective(
            participant_id="dba",
            participant_type="bot",
            role="consultant",
            summary="从数据库角度，该方案会增加索引维护成本。",
            confidence=0.85,
            evidence=["涉及读多写少场景", "索引命中率可接受"],
            status="completed",
        )

        assert perspective.confidence == 0.85
        assert len(perspective.evidence) == 2

    def test_perspective_status_values(self):
        """测试 Perspective status 有效值"""
        from src.domain.models.fusion_result import Perspective

        valid_statuses = ["completed", "timed_out", "failed", "skipped"]

        for status in valid_statuses:
            perspective = Perspective(
                participant_id="test",
                participant_type="bot",
                role="consultant",
                summary="test",
                status=status,
            )
            assert perspective.status == status

    def test_perspective_invalid_status(self):
        """测试无效 status 被拒绝"""
        from src.domain.models.fusion_result import Perspective

        with pytest.raises(ValidationError):
            Perspective(
                participant_id="test",
                participant_type="bot",
                role="consultant",
                summary="test",
                status="invalid_status",
            )

    def test_perspective_participant_type_values(self):
        """测试 participant_type 有效值"""
        from src.domain.models.fusion_result import Perspective

        for p_type in ["bot", "human", "system"]:
            perspective = Perspective(
                participant_id="test",
                participant_type=p_type,
                role="consultant",
                summary="test",
                status="completed",
            )
            assert perspective.participant_type == p_type

    def test_perspective_role_values(self):
        """测试 role 有效值"""
        from src.domain.models.fusion_result import Perspective

        for role in ["driver", "consultant", "observer"]:
            perspective = Perspective(
                participant_id="test",
                participant_type="bot",
                role=role,
                summary="test",
                status="completed",
            )
            assert perspective.role == role

    def test_perspective_confidence_range(self):
        """测试 confidence 范围"""
        from src.domain.models.fusion_result import Perspective

        # 有效范围
        for confidence in [0, 0.5, 1.0]:
            perspective = Perspective(
                participant_id="test",
                participant_type="bot",
                role="consultant",
                summary="test",
                status="completed",
                confidence=confidence,
            )
            assert perspective.confidence == confidence

        # 无效范围
        with pytest.raises(ValidationError):
            Perspective(
                participant_id="test",
                participant_type="bot",
                role="consultant",
                summary="test",
                status="completed",
                confidence=1.5,
            )


class TestRecommendation:
    """Recommendation 测试"""

    def test_create_recommendation(self):
        """测试创建 Recommendation"""
        from src.domain.models.fusion_result import Recommendation

        recommendation = Recommendation(
            summary="方案总体可行，但需要补充安全评估。",
            decision="conditional_yes",
            risks=["权限边界不足", "索引维护开销上升"],
            next_actions=["补齐权限校验设计", "评估索引写入成本"],
        )

        assert recommendation.summary == "方案总体可行，但需要补充安全评估。"
        assert recommendation.decision == "conditional_yes"
        assert len(recommendation.risks) == 2
        assert len(recommendation.next_actions) == 2

    def test_recommendation_decision_values(self):
        """测试 decision 有效值"""
        from src.domain.models.fusion_result import Recommendation

        for decision in ["yes", "no", "conditional_yes", "needs_more_information"]:
            rec = Recommendation(
                summary="test",
                decision=decision,
                risks=[],
                next_actions=[],
            )
            assert rec.decision == decision

    def test_recommendation_invalid_decision(self):
        """测试无效 decision 被拒绝"""
        from src.domain.models.fusion_result import Recommendation

        with pytest.raises(ValidationError):
            Recommendation(
                summary="test",
                decision="invalid",
                risks=[],
                next_actions=[],
            )


class TestFusionTiming:
    """FusionTiming 测试"""

    def test_create_timing(self):
        """测试创建 FusionTiming"""
        from src.domain.models.fusion_result import FusionTiming
        from datetime import datetime

        started = datetime(2026, 3, 21, 10, 0, 0)
        finished = datetime(2026, 3, 21, 10, 0, 8)

        timing = FusionTiming(
            started_at=started,
            finished_at=finished,
            duration_ms=8000,
        )

        assert timing.started_at == started
        assert timing.finished_at == finished
        assert timing.duration_ms == 8000

    def test_timing_duration_non_negative(self):
        """测试 duration_ms 非负"""
        from src.domain.models.fusion_result import FusionTiming

        with pytest.raises(ValidationError):
            FusionTiming(
                started_at="2026-03-21T10:00:00Z",
                finished_at="2026-03-21T10:00:08Z",
                duration_ms=-1,
            )


class TestFusionResult:
    """FusionResult 测试"""

    def test_create_minimal_result(self):
        """测试创建最小 FusionResult"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="这个方案是否可行",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
        )

        assert result.group_id == "grp-001"
        assert result.fusion_id == "fus-001"
        assert result.question == "这个方案是否可行"
        assert result.perspectives == []
        assert result.recommendation is None
        assert result.partial_success is False
        assert result.driver_bot_id is None

    def test_create_full_result(self):
        """测试创建完整 FusionResult"""
        from src.domain.models.fusion_result import (
            FusionResult,
            Perspective,
            Recommendation,
            FusionTiming,
        )

        perspective = Perspective(
            participant_id="dba",
            participant_type="bot",
            role="consultant",
            summary="从数据库角度整体可行。",
            confidence=0.85,
            status="completed",
        )

        recommendation = Recommendation(
            summary="方案可行",
            decision="yes",
            risks=[],
            next_actions=[],
        )

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="这个方案是否可行",
            driver_bot_id="zhangsan",
            perspectives=[perspective],
            recommendation=recommendation,
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
        )

        assert result.driver_bot_id == "zhangsan"
        assert len(result.perspectives) == 1
        assert result.recommendation is not None

    def test_result_partial_success_true(self):
        """测试 partial_success 为 True"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=True,
            warnings=["participant security timed out"],
            errors=[],
            timing=timing,
        )

        assert result.partial_success is True
        assert len(result.warnings) == 1

    def test_result_with_errors(self):
        """测试包含 errors 的结果"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=True,
            warnings=[],
            errors=["participant dba failed to respond"],
            timing=timing,
        )

        assert len(result.errors) == 1

    def test_result_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        with pytest.raises(ValidationError):
            FusionResult(
                group_id="grp-001",
                fusion_id="fus-001",
                question="test",
                perspectives=[],
                partial_success=False,
                warnings=[],
                errors=[],
                timing=timing,
                unknown_field="should_fail",
            )

    def test_result_is_success_property(self):
        """测试 is_success 属性"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        # 完全成功
        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
        )
        assert result.is_success is True

        # 部分成功
        result_partial = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=True,
            warnings=["warning"],
            errors=[],
            timing=timing,
        )
        assert result_partial.is_success is True

        # 有错误但部分成功
        result_with_errors = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=True,
            warnings=[],
            errors=["error"],
            timing=timing,
        )
        assert result_with_errors.is_success is True


# =============================================================================
# G2: Conflict Alignment Tests
# =============================================================================


class TestPerspectiveG2:
    """Perspective G2 扩展字段测试"""

    def test_perspective_key_points(self):
        """测试 Perspective 的 key_points 字段"""
        from src.domain.models.fusion_result import Perspective

        perspective = Perspective(
            participant_id="zhangsan",
            participant_type="bot",
            role="consultant",
            summary="开发者视角：当前代码实现为60分钟超时",
            key_points=["兼容旧系统", "避免大规模重构"],
            status="completed",
        )

        assert perspective.key_points == ["兼容旧系统", "避免大规模重构"]

    def test_perspective_concerns(self):
        """测试 Perspective 的 concerns 字段"""
        from src.domain.models.fusion_result import Perspective

        perspective = Perspective(
            participant_id="lisi",
            participant_type="bot",
            role="consultant",
            summary="PM视角：PRD要求30分钟超时",
            concerns=["用户等待焦虑"],
            status="completed",
        )

        assert perspective.concerns == ["用户等待焦虑"]

    def test_perspective_flexibility(self):
        """测试 Perspective 的 flexibility 字段"""
        from src.domain.models.fusion_result import Perspective

        perspective = Perspective(
            participant_id="anquan",
            participant_type="bot",
            role="consultant",
            summary="安全视角：60分钟存在会话劫持风险",
            flexibility="如果必须60分钟，需加二次确认",
            status="completed",
        )

        assert perspective.flexibility == "如果必须60分钟，需加二次确认"

    def test_perspective_g2_fields_defaults(self):
        """测试 G2 字段默认值"""
        from src.domain.models.fusion_result import Perspective

        perspective = Perspective(
            participant_id="test",
            participant_type="bot",
            role="consultant",
            summary="test",
            status="completed",
        )

        # G2 字段默认应为空
        assert perspective.key_points == []
        assert perspective.concerns == []
        assert perspective.flexibility is None

    def test_perspective_full_g2_data(self):
        """测试完整的 G2 Perspective"""
        from src.domain.models.fusion_result import Perspective

        perspective = Perspective(
            participant_id="zhangsan",
            participant_type="bot",
            role="driver",
            summary="开发者视角：当前代码实现为60分钟超时",
            confidence=0.85,
            evidence=["代码审查记录"],
            key_points=["兼容旧系统", "避免大规模重构"],
            concerns=["改造成本", "上线风险"],
            flexibility="如果安全不通过，愿意分阶段改造",
            status="completed",
        )

        assert perspective.key_points == ["兼容旧系统", "避免大规模重构"]
        assert perspective.concerns == ["改造成本", "上线风险"]
        assert perspective.flexibility == "如果安全不通过，愿意分阶段改造"


class TestFusionResultG2:
    """FusionResult G2 扩展字段测试"""

    def test_result_fusion_mode_default(self):
        """测试 fusion_mode 默认值"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
        )

        # 默认应该是 agent（G1）
        assert result.fusion_mode == "agent"

    def test_result_fusion_mode_conflict_alignment(self):
        """测试 fusion_mode 为 conflict_alignment（G2）"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="如何协调代码与PRD的超时时间冲突？",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="conflict_alignment",
        )

        assert result.fusion_mode == "conflict_alignment"

    def test_result_conflicts_field(self):
        """测试 conflicts 字段"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming
        from src.domain.models.fusion_conflict import FusionConflict

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        conflict = FusionConflict(
            parties=["zhangsan", "lisi"],
            issue="超时时间不一致",
            positions=["60分钟（兼容）", "30分钟（PRD）"],
            severity="medium",
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="conflict_alignment",
            conflicts=[conflict],
        )

        assert len(result.conflicts) == 1
        assert result.conflicts[0].issue == "超时时间不一致"

    def test_result_alignment_points_field(self):
        """测试 alignment_points 字段"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        alignment = FusionAlignmentPoint(
            summary="三方都认同需要兼顾用户体验和安全",
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="conflict_alignment",
            alignment_points=[alignment],
        )

        assert len(result.alignment_points) == 1
        assert result.alignment_points[0].summary == "三方都认同需要兼顾用户体验和安全"

    def test_result_key_insights_field(self):
        """测试 key_insights 字段"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="conflict_alignment",
            key_insights=["技术妥协可行性高", "安全风险可通过机制补偿"],
        )

        assert result.key_insights == ["技术妥协可行性高", "安全风险可通过机制补偿"]

    def test_result_g1_mode_has_empty_g2_fields(self):
        """测试 G1 模式下 G2 字段为空"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
        )

        # G1 模式下 G2 字段应为空
        assert result.fusion_mode == "agent"
        assert result.conflicts == []
        assert result.alignment_points == []
        assert result.key_insights == []

    def test_result_full_g2_response(self):
        """测试完整的 G2 响应"""
        from src.domain.models.fusion_result import (
            FusionResult,
            Perspective,
            Recommendation,
            FusionTiming,
        )
        from src.domain.models.fusion_conflict import FusionConflict
        from src.domain.models.fusion_alignment import FusionAlignmentPoint

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

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
        ]

        conflicts = [
            FusionConflict(
                parties=["zhangsan", "lisi"],
                issue="超时时间不一致",
                positions=["60分钟", "30分钟"],
                severity="medium",
            ),
        ]

        alignment_points = [
            FusionAlignmentPoint(
                summary="三方都认同需要兼顾用户体验和安全",
            ),
        ]

        recommendation = Recommendation(
            summary="建议分两阶段实施",
            decision="conditional_yes",
            risks=["安全基线风险"],
            next_actions=["评估改造范围"],
        )

        result = FusionResult(
            group_id="grp-fusion-001",
            fusion_id="fus-001",
            question="如何协调代码与PRD的超时时间冲突？",
            driver_bot_id="zhangsan",
            perspectives=perspectives,
            recommendation=recommendation,
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="conflict_alignment",
            conflicts=conflicts,
            alignment_points=alignment_points,
            key_insights=["技术妥协可行性高"],
        )

        assert result.fusion_mode == "conflict_alignment"
        assert len(result.conflicts) == 1
        assert len(result.alignment_points) == 1
        assert len(result.key_insights) == 1
        assert result.perspectives[0].key_points == ["兼容旧系统", "避免大规模重构"]


# =============================================================================
# G5: Expert Diagnosis Tests
# =============================================================================


class TestPerspectiveG5:
    """Perspective G5 扩展字段测试"""

    def test_perspective_role_expert(self):
        """测试 Perspective 的 role 支持 expert"""
        from src.domain.models.fusion_result import Perspective

        perspective = Perspective(
            participant_id="anquan",
            participant_type="bot",
            role="expert",
            summary="安全专家视角：存在会话劫持风险",
            status="completed",
        )

        assert perspective.role == "expert"

    def test_perspective_role_expert_with_confidence(self):
        """测试 expert 角色包含置信度"""
        from src.domain.models.fusion_result import Perspective

        perspective = Perspective(
            participant_id="fawu",
            participant_type="bot",
            role="expert",
            summary="法务专家视角：数据存储存在合规问题",
            confidence=0.95,
            status="completed",
        )

        assert perspective.role == "expert"
        assert perspective.confidence == 0.95

    def test_perspective_all_roles_including_expert(self):
        """测试所有角色包括 expert"""
        from src.domain.models.fusion_result import Perspective

        for role in ["driver", "consultant", "observer", "expert"]:
            perspective = Perspective(
                participant_id="test",
                participant_type="bot",
                role=role,
                summary="test",
                status="completed",
            )
            assert perspective.role == role

    def test_perspective_invalid_role_still_rejected(self):
        """测试无效 role 仍被拒绝"""
        from src.domain.models.fusion_result import Perspective

        with pytest.raises(ValidationError):
            Perspective(
                participant_id="test",
                participant_type="bot",
                role="invalid_role",
                summary="test",
                status="completed",
            )


class TestFusionResultG5:
    """FusionResult G5 扩展字段测试"""

    def test_result_fusion_mode_expert_diagnosis(self):
        """测试 fusion_mode 为 expert_diagnosis（G5）"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="这个方案是否可以上线？",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="expert_diagnosis",
        )

        assert result.fusion_mode == "expert_diagnosis"

    def test_result_risk_assessment_field(self):
        """测试 risk_assessment 字段"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming
        from src.domain.models.expert_risk_assessment import RiskAssessment, RiskLevel

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        risk_assessment = RiskAssessment(
            overall=RiskLevel.HIGH,
            categories={
                "security": RiskLevel.CRITICAL,
                "legal": RiskLevel.MEDIUM,
            },
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="expert_diagnosis",
            risk_assessment=risk_assessment,
        )

        assert result.risk_assessment is not None
        assert result.risk_assessment.overall == RiskLevel.HIGH

    def test_result_critical_issues_field(self):
        """测试 critical_issues 字段"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming
        from src.domain.models.expert_diagnosis import CriticalIssue
        from src.domain.models.expert_risk_assessment import RiskLevel

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        critical_issues = [
            CriticalIssue(
                issue="支付接口缺少签名验证",
                severity=RiskLevel.CRITICAL,
                domain="security",
                source="anquan",
            ),
        ]

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="expert_diagnosis",
            critical_issues=critical_issues,
        )

        assert len(result.critical_issues) == 1
        assert result.critical_issues[0].issue == "支付接口缺少签名验证"

    def test_result_recommendations_field(self):
        """测试 recommendations 字段（G5 专家建议列表）"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming
        from src.domain.models.expert_diagnosis import ExpertRecommendation, Priority

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        recommendations = [
            ExpertRecommendation(
                priority=Priority.P0,
                action="立即修复支付接口签名验证",
                owner="security_team",
                domain="security",
            ),
            ExpertRecommendation(
                priority=Priority.P1,
                action="完成数据合规性整改",
                owner="legal_team",
                domain="legal",
            ),
        ]

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="expert_diagnosis",
            recommendations=recommendations,
        )

        assert len(result.recommendations) == 2
        assert result.recommendations[0].priority == Priority.P0

    def test_result_go_live_conditions_field(self):
        """测试 go_live_conditions 字段"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        go_live_conditions = [
            "完成支付接口签名验证",
            "通过安全渗透测试",
            "完成数据合规审查",
        ]

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="expert_diagnosis",
            go_live_conditions=go_live_conditions,
        )

        assert len(result.go_live_conditions) == 3
        assert "完成支付接口签名验证" in result.go_live_conditions

    def test_result_summary_field(self):
        """测试 summary 字段（G5 诊断摘要）"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="expert_diagnosis",
            summary="综合安全、法务、DBA 视角，建议暂缓上线，优先处理安全问题。",
        )

        assert result.summary == "综合安全、法务、DBA 视角，建议暂缓上线，优先处理安全问题。"

    def test_result_g1_mode_has_empty_g5_fields(self):
        """测试 G1 模式下 G5 字段为空"""
        from src.domain.models.fusion_result import FusionResult, FusionTiming

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        result = FusionResult(
            group_id="grp-001",
            fusion_id="fus-001",
            question="test",
            perspectives=[],
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
        )

        # G1 模式下 G5 字段应为空
        assert result.fusion_mode == "agent"
        assert result.risk_assessment is None
        assert result.critical_issues == []
        assert result.recommendations == []
        assert result.go_live_conditions == []
        assert result.summary is None

    def test_result_full_g5_response(self):
        """测试完整的 G5 响应"""
        from src.domain.models.fusion_result import (
            FusionResult,
            Perspective,
            Recommendation,
            FusionTiming,
        )
        from src.domain.models.expert_risk_assessment import RiskAssessment, RiskLevel
        from src.domain.models.expert_diagnosis import (
            CriticalIssue,
            ExpertRecommendation,
            Priority,
        )

        timing = FusionTiming(
            started_at="2026-03-21T10:00:00Z",
            finished_at="2026-03-21T10:00:08Z",
            duration_ms=8000,
        )

        # 专家视角
        perspectives = [
            Perspective(
                participant_id="anquan",
                participant_type="bot",
                role="expert",
                summary="安全视角：支付接口存在严重漏洞",
                confidence=0.95,
                status="completed",
            ),
            Perspective(
                participant_id="fawu",
                participant_type="bot",
                role="expert",
                summary="法务视角：数据存储合规性问题",
                confidence=0.90,
                status="completed",
            ),
        ]

        # 风险评估
        risk_assessment = RiskAssessment(
            overall=RiskLevel.HIGH,
            categories={
                "security": RiskLevel.CRITICAL,
                "legal": RiskLevel.MEDIUM,
            },
        )

        # 关键问题
        critical_issues = [
            CriticalIssue(
                issue="支付接口缺少签名验证",
                severity=RiskLevel.CRITICAL,
                domain="security",
                source="anquan",
            ),
        ]

        # 专家建议
        recommendations = [
            ExpertRecommendation(
                priority=Priority.P0,
                action="立即修复支付接口签名验证",
                owner="security_team",
                domain="security",
            ),
        ]

        # 上线条件
        go_live_conditions = [
            "完成支付接口签名验证",
            "通过安全渗透测试",
        ]

        # G1/G2 的单一建议（G5 可能也有）
        recommendation = Recommendation(
            summary="建议暂缓上线",
            decision="conditional_yes",
            risks=["安全漏洞未修复"],
            next_actions=["修复支付接口"],
        )

        result = FusionResult(
            group_id="grp-expert-001",
            fusion_id="fus-001",
            question="这个方案是否可以上线？",
            driver_bot_id="dba",
            perspectives=perspectives,
            recommendation=recommendation,
            partial_success=False,
            warnings=[],
            errors=[],
            timing=timing,
            fusion_mode="expert_diagnosis",
            risk_assessment=risk_assessment,
            critical_issues=critical_issues,
            recommendations=recommendations,
            go_live_conditions=go_live_conditions,
            summary="综合各方专家意见，建议暂缓上线，优先处理安全问题。",
        )

        # 验证 G5 特有字段
        assert result.fusion_mode == "expert_diagnosis"
        assert result.risk_assessment.overall == RiskLevel.HIGH
        assert len(result.critical_issues) == 1
        assert len(result.recommendations) == 1
        assert result.recommendations[0].priority == Priority.P0
        assert len(result.go_live_conditions) == 2
        assert result.summary == "综合各方专家意见，建议暂缓上线，优先处理安全问题。"

        # 验证视角包含 expert 角色
        assert result.perspectives[0].role == "expert"
        assert result.perspectives[1].role == "expert"