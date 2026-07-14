"""
Tests for GroupFusionService

G1: Fusion Entry Layer

测试 GroupFusionService 的核心业务逻辑。
"""

from __future__ import annotations

import pytest
from datetime import datetime

from src.domain.models.fusion_request import FusionRequest, FuseOptions
from src.domain.models.fusion_result import (
    FusionResult,
    Perspective,
    Recommendation,
)
from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext


# =============================================================================
# Fake Provider for Testing
# =============================================================================

class FakePerspectiveProvider:
    """测试用 Fake Perspective Provider"""

    def __init__(self, responses: dict[str, Perspective] | None = None):
        """
        初始化

        Args:
            responses: participant_id -> Perspective 映射
        """
        self.responses = responses or {}
        self.call_log: list[PerspectiveContext] = []

    def collect(self, context: PerspectiveContext) -> Perspective:
        """收集视角"""
        self.call_log.append(context)

        if context.participant_id in self.responses:
            return self.responses[context.participant_id]

        # 默认返回一个成功的视角
        return Perspective(
            participant_id=context.participant_id,
            participant_type="bot",
            role="consultant",
            summary=f"从 {context.participant_id} 角度，{context.question}",
            confidence=0.8,
            status="completed",
        )


class TimeoutPerspectiveProvider:
    """模拟超时的 Provider"""

    def collect(self, context: PerspectiveContext) -> Perspective:
        return Perspective(
            participant_id=context.participant_id,
            participant_type="bot",
            role="consultant",
            summary="",
            status="timed_out",
        )


class FailedPerspectiveProvider:
    """模拟失败的 Provider"""

    def collect(self, context: PerspectiveContext) -> Perspective:
        return Perspective(
            participant_id=context.participant_id,
            participant_type="bot",
            role="consultant",
            summary="",
            status="failed",
        )


# =============================================================================
# Module Tests
# =============================================================================

class TestGroupFusionServiceModule:
    """模块存在性测试"""

    def test_module_exists(self):
        """测试 group_fusion_service 模块存在"""
        import importlib

        module = importlib.import_module("src.application.services.group_fusion_service")
        assert module is not None

    def test_service_class_exists(self):
        """测试 GroupFusionService 类存在"""
        from src.application.services.group_fusion_service import GroupFusionService

        assert GroupFusionService is not None


# =============================================================================
# Basic Service Tests
# =============================================================================

class TestGroupFusionServiceBasic:
    """基本服务测试"""

    def test_service_accepts_provider(self):
        """测试服务接受 Provider 注入"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        assert service is not None

    def test_service_fuse_returns_result(self):
        """测试 fuse 返回 FusionResult"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="这个方案是否可行",
            participants=["dba", "security"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert isinstance(result, FusionResult)
        assert result.group_id == "grp-001"
        assert result.question == "这个方案是否可行"

    def test_service_generates_fusion_id(self):
        """测试服务生成 fusion_id"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.fusion_id is not None
        assert result.fusion_id.startswith("fus-")


# =============================================================================
# Participant Resolution Tests
# =============================================================================

class TestParticipantResolution:
    """参与者解析测试"""

    def test_service_calls_provider_for_each_participant(self):
        """测试为每个 participant 调用 provider"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba", "security", "ops"],
        )

        service.fuse(request, group_id="grp-001")

        assert len(provider.call_log) == 3

    def test_service_collects_perspectives_for_all_participants(self):
        """测试为所有 participant 收集视角"""
        from src.application.services.group_fusion_service import GroupFusionService

        responses = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="从数据库角度可行",
                confidence=0.85,
                status="completed",
            ),
            "security": Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="从安全角度需要加固",
                confidence=0.9,
                status="completed",
            ),
        }

        provider = FakePerspectiveProvider(responses=responses)
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba", "security"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert len(result.perspectives) == 2
        assert result.perspectives[0].participant_id == "dba"
        assert result.perspectives[1].participant_id == "security"


# =============================================================================
# Driver Bot Tests
# =============================================================================

class TestDriverBotResolution:
    """Driver bot 解析测试"""

    def test_explicit_driver_bot_id(self):
        """测试显式指定 driver_bot_id"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["zhangsan", "dba"],
            driver_bot_id="zhangsan",
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.driver_bot_id == "zhangsan"

    def test_implicit_driver_bot_first_participant(self):
        """测试隐式使用第一个 participant 作为 driver"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["zhangsan", "dba"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.driver_bot_id == "zhangsan"


# =============================================================================
# Partial Success Tests
# =============================================================================

class TestPartialSuccess:
    """部分成功测试"""

    def test_partial_success_when_one_times_out(self):
        """测试一个 participant 超时时部分成功"""
        from src.application.services.group_fusion_service import GroupFusionService

        responses = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="从数据库角度可行",
                confidence=0.85,
                status="completed",
            ),
            "security": Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="",
                status="timed_out",
            ),
        }

        provider = FakePerspectiveProvider(responses=responses)
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba", "security"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.partial_success is True
        assert len(result.warnings) > 0

    def test_partial_success_when_one_fails(self):
        """测试一个 participant 失败时部分成功"""
        from src.application.services.group_fusion_service import GroupFusionService

        responses = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="从数据库角度可行",
                confidence=0.85,
                status="completed",
            ),
            "security": Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="",
                status="failed",
            ),
        }

        provider = FakePerspectiveProvider(responses=responses)
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba", "security"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.partial_success is True

    def test_all_success_not_partial(self):
        """测试所有成功时不是部分成功"""
        from src.application.services.group_fusion_service import GroupFusionService

        responses = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="ok",
                status="completed",
            ),
        }

        provider = FakePerspectiveProvider(responses=responses)
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.partial_success is False


# =============================================================================
# Recommendation Tests
# =============================================================================

class TestRecommendation:
    """建议生成测试"""

    def test_recommendation_generated_when_all_success(self):
        """测试全部成功时生成 recommendation"""
        from src.application.services.group_fusion_service import GroupFusionService

        responses = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="可行",
                confidence=0.85,
                status="completed",
            ),
        }

        provider = FakePerspectiveProvider(responses=responses)
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba"],
            options=FuseOptions(include_recommendation=True),
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.recommendation is not None

    def test_no_recommendation_when_disabled(self):
        """测试禁用时无 recommendation"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba"],
            options=FuseOptions(include_recommendation=False),
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.recommendation is None

    def test_recommendation_decision_based_on_perspectives(self):
        """测试 recommendation 决策基于 perspectives"""
        from src.application.services.group_fusion_service import GroupFusionService

        responses = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="可行",
                confidence=0.85,
                status="completed",
            ),
            "security": Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="需要加固",
                confidence=0.9,
                status="completed",
            ),
        }

        provider = FakePerspectiveProvider(responses=responses)
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba", "security"],
            options=FuseOptions(include_recommendation=True),
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.recommendation is not None
        assert result.recommendation.decision in [
            "yes", "no", "conditional_yes", "needs_more_information"
        ]


# =============================================================================
# Timing Tests
# =============================================================================

class TestTiming:
    """计时测试"""

    def test_timing_populated(self):
        """测试计时信息填充"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.timing is not None
        assert result.timing.started_at is not None
        assert result.timing.finished_at is not None
        assert result.timing.duration_ms >= 0

    def test_timing_order_correct(self):
        """测试开始时间早于结束时间"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.timing.finished_at >= result.timing.started_at


# =============================================================================
# Group Validation Tests
# =============================================================================

class TestGroupValidation:
    """Group 校验测试"""

    def test_group_id_in_result(self):
        """测试 group_id 出现在结果中"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba"],
        )

        result = service.fuse(request, group_id="grp-test-123")

        assert result.group_id == "grp-test-123"


# =============================================================================
# Warnings and Errors Tests
# =============================================================================

class TestWarningsAndErrors:
    """警告和错误测试"""

    def test_warnings_include_timed_out_participants(self):
        """测试警告包含超时 participant"""
        from src.application.services.group_fusion_service import GroupFusionService

        responses = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="",
                status="timed_out",
            ),
        }

        provider = FakePerspectiveProvider(responses=responses)
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert any("timed out" in w.lower() for w in result.warnings)

    def test_warnings_include_failed_participants(self):
        """测试警告包含失败 participant"""
        from src.application.services.group_fusion_service import GroupFusionService

        responses = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="",
                status="failed",
            ),
            "security": Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="ok",
                status="completed",
            ),
        }

        provider = FakePerspectiveProvider(responses=responses)
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba", "security"],
        )

        result = service.fuse(request, group_id="grp-001")

        assert any("failed" in w.lower() for w in result.warnings)


# =============================================================================
# G2: Conflict Alignment Mode Tests
# =============================================================================


class TestFusionModeDispatch:
    """融合模式分发测试"""

    def test_g1_mode_uses_original_logic(self):
        """测试 G1 模式使用原有逻辑"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="G1 test",
            participants=["dba"],
            fusion_mode="agent",  # G1 模式
        )

        result = service.fuse(request, group_id="grp-001")

        # G1 模式下 G2 字段为空
        assert result.fusion_mode == "agent"
        assert result.conflicts == []
        assert result.alignment_points == []
        assert result.key_insights == []

    def test_g2_mode_dispatches_to_conflict_alignment(self):
        """测试 G2 模式分发到冲突对齐逻辑"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="如何协调代码与PRD的超时时间冲突？",
            participants=["zhangsan", "lisi"],
            fusion_mode="conflict_alignment",  # G2 模式
        )

        result = service.fuse(request, group_id="grp-001")

        # G2 模式
        assert result.fusion_mode == "conflict_alignment"
        # G2 字段存在（可能为空，但字段存在）
        assert hasattr(result, "conflicts")
        assert hasattr(result, "alignment_points")
        assert hasattr(result, "key_insights")

    def test_default_fusion_mode_is_agent(self):
        """测试默认模式是 agent（G1）"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["dba"],
            # 不指定 fusion_mode
        )

        result = service.fuse(request, group_id="grp-001")

        # 默认应该是 agent
        assert result.fusion_mode == "agent"


class TestG1G2Isolation:
    """G1/G2 隔离测试"""

    def test_g1_not_polluted_by_g2_fields(self):
        """测试 G1 不被 G2 字段污染"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        # G1 请求
        g1_request = FusionRequest(
            question="G1 question",
            participants=["dba", "security"],
            fusion_mode="agent",
        )

        g1_result = service.fuse(g1_request, group_id="grp-g1")

        # G1 结果验证
        assert g1_result.fusion_mode == "agent"
        assert g1_result.conflicts == []
        assert g1_result.alignment_points == []
        assert g1_result.key_insights == []
        # G1 核心字段正常
        assert g1_result.perspectives is not None
        assert g1_result.recommendation is not None

    def test_g1_g2_results_are_independent(self):
        """测试 G1 和 G2 结果相互独立"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        # G2 请求
        g2_request = FusionRequest(
            question="G2 question",
            participants=["a", "b"],
            fusion_mode="conflict_alignment",
        )

        g2_result = service.fuse(g2_request, group_id="grp-g2")

        # G1 请求
        g1_request = FusionRequest(
            question="G1 question",
            participants=["c", "d"],
            fusion_mode="agent",
        )

        g1_result = service.fuse(g1_request, group_id="grp-g1")

        # G2 结果应该是 G2 模式
        assert g2_result.fusion_mode == "conflict_alignment"
        # G1 结果应该是 agent 模式
        assert g1_result.fusion_mode == "agent"
        assert g1_result.conflicts == []


class TestG2WithConflictAlignmentService:
    """G2 与 ConflictAlignmentService 集成测试"""

    def test_g2_uses_conflict_alignment_service_when_injected(self):
        """测试 G2 使用注入的 ConflictAlignmentService"""
        from src.application.services.group_fusion_service import GroupFusionService
        from unittest.mock import Mock

        provider = FakePerspectiveProvider()
        mock_conflict_service = Mock()

        # 设置 mock 返回值
        mock_result = Mock()
        mock_result.fusion_mode = "conflict_alignment"
        mock_result.conflicts = []
        mock_result.alignment_points = []
        mock_result.key_insights = []
        mock_result.perspectives = []
        mock_result.recommendation = None
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.question = "test"
        mock_result.driver_bot_id = None
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"

        mock_conflict_service.align.return_value = mock_result

        service = GroupFusionService(
            provider=provider,
            conflict_alignment_service=mock_conflict_service,
        )

        request = FusionRequest(
            question="G2 test",
            participants=["a", "b"],
            fusion_mode="conflict_alignment",
        )

        result = service.fuse(request, group_id="grp-001")

        # ConflictAlignmentService 应该被调用
        mock_conflict_service.align.assert_called_once()
        assert result.fusion_mode == "conflict_alignment"


class TestG2PartialSuccess:
    """G2 partial success 测试"""

    def test_g2_partial_success_propagates(self):
        """测试 G2 partial_success 正确传播"""
        from src.application.services.group_fusion_service import GroupFusionService

        # 创建一个会有失败视角的 provider
        responses = {
            "a": Perspective(
                participant_id="a",
                participant_type="bot",
                role="consultant",
                summary="ok",
                status="completed",
            ),
            "b": Perspective(
                participant_id="b",
                participant_type="bot",
                role="consultant",
                summary="",
                status="failed",
            ),
        }

        provider = FakePerspectiveProvider(responses=responses)
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="test",
            participants=["a", "b"],
            fusion_mode="conflict_alignment",
        )

        result = service.fuse(request, group_id="grp-001")

        # partial_success 应该为 True
        assert result.partial_success is True
        # 应该有警告
        assert len(result.warnings) > 0


# =============================================================================
# G5: Expert Diagnosis Tests
# =============================================================================


class TestG5ModeDispatch:
    """G5 模式分发测试"""

    def test_g5_mode_dispatches_to_expert_diagnosis(self):
        """测试 G5 模式分发到专家会诊逻辑"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="这个方案是否可以上线？",
            participants=["anquan", "fawu", "dba"],
            fusion_mode="expert_diagnosis",  # G5 模式
        )

        result = service.fuse(request, group_id="grp-001")

        # G5 模式
        assert result.fusion_mode == "expert_diagnosis"
        # G5 字段存在
        assert hasattr(result, "risk_assessment")
        assert hasattr(result, "critical_issues")
        assert hasattr(result, "recommendations")
        assert hasattr(result, "go_live_conditions")
        assert hasattr(result, "summary")

    def test_g5_result_has_g5_fields_populated(self):
        """测试 G5 结果包含 G5 字段"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="上线评审",
            participants=["anquan", "dba"],
            fusion_mode="expert_diagnosis",
        )

        result = service.fuse(request, group_id="grp-001")

        assert result.fusion_mode == "expert_diagnosis"
        assert result.risk_assessment is not None
        # G5 字段被填充（可能为空列表，但不应该是 None）
        assert result.critical_issues is not None
        assert result.recommendations is not None
        assert result.go_live_conditions is not None
        assert result.summary is not None


class TestG1G2G5ThreeWayIsolation:
    """G1/G2/G5 三模式隔离测试（回归测试）"""

    def test_g1_not_polluted_by_g5_fields(self):
        """测试 G1 不被 G5 字段污染"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        # G1 请求
        g1_request = FusionRequest(
            question="G1 question",
            participants=["dba", "dev"],
            fusion_mode="agent",
        )

        g1_result = service.fuse(g1_request, group_id="grp-g1")

        # G1 结果验证
        assert g1_result.fusion_mode == "agent"
        # G5 字段应为默认值（空或 None）
        assert g1_result.risk_assessment is None
        assert g1_result.critical_issues == []
        assert g1_result.recommendations == []
        assert g1_result.go_live_conditions == []
        assert g1_result.summary is None
        # G1 核心字段正常
        assert g1_result.perspectives is not None
        assert g1_result.recommendation is not None

    def test_g2_not_polluted_by_g5_fields(self):
        """测试 G2 不被 G5 字段污染"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        # G2 请求
        g2_request = FusionRequest(
            question="G2 question",
            participants=["a", "b"],
            fusion_mode="conflict_alignment",
        )

        g2_result = service.fuse(g2_request, group_id="grp-g2")

        # G2 结果验证
        assert g2_result.fusion_mode == "conflict_alignment"
        # G5 字段应为默认值（空或 None）
        assert g2_result.risk_assessment is None
        assert g2_result.critical_issues == []
        assert g2_result.recommendations == []
        assert g2_result.go_live_conditions == []
        assert g2_result.summary is None
        # G2 字段正常
        assert hasattr(g2_result, "conflicts")
        assert hasattr(g2_result, "alignment_points")
        assert hasattr(g2_result, "key_insights")

    def test_g5_only_activates_in_expert_diagnosis_mode(self):
        """测试 G5 字段只在 expert_diagnosis 模式生效"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        # 三种模式的请求
        modes = ["agent", "conflict_alignment", "expert_diagnosis"]
        results = {}

        for mode in modes:
            request = FusionRequest(
                question=f"{mode} test",
                participants=["a", "b"],
                fusion_mode=mode,  # type: ignore
            )
            results[mode] = service.fuse(request, group_id=f"grp-{mode}")

        # G1: G5 字段为空
        assert results["agent"].risk_assessment is None
        assert results["agent"].critical_issues == []

        # G2: G5 字段为空
        assert results["conflict_alignment"].risk_assessment is None
        assert results["conflict_alignment"].critical_issues == []

        # G5: G5 字段填充
        assert results["expert_diagnosis"].risk_assessment is not None
        assert results["expert_diagnosis"].summary is not None

    def test_g1_g2_g5_results_are_independent(self):
        """测试 G1/G2/G5 结果相互独立"""
        from src.application.services.group_fusion_service import GroupFusionService

        provider = FakePerspectiveProvider()
        service = GroupFusionService(provider=provider)

        # 连续执行 G1 -> G2 -> G5 -> G1
        results = []
        modes = ["agent", "conflict_alignment", "expert_diagnosis", "agent"]

        for mode in modes:
            request = FusionRequest(
                question=f"{mode} test",
                participants=["a", "b"],
                fusion_mode=mode,  # type: ignore
            )
            results.append(service.fuse(request, group_id=f"grp-{mode}"))

        # 验证模式隔离
        assert results[0].fusion_mode == "agent"
        assert results[1].fusion_mode == "conflict_alignment"
        assert results[2].fusion_mode == "expert_diagnosis"
        assert results[3].fusion_mode == "agent"

        # 最后一个 G1 不应该受到之前 G5 的影响
        assert results[3].risk_assessment is None
        assert results[3].critical_issues == []


class TestG5WithExpertDiagnosisService:
    """G5 与 ExpertDiagnosisService 集成测试"""

    def test_g5_uses_expert_diagnosis_service_when_injected(self):
        """测试 G5 使用注入的 ExpertDiagnosisService"""
        from src.application.services.group_fusion_service import GroupFusionService
        from unittest.mock import Mock

        provider = FakePerspectiveProvider()
        mock_expert_service = Mock()

        # 设置 mock 返回值
        mock_result = Mock()
        mock_result.fusion_mode = "expert_diagnosis"
        mock_result.risk_assessment = None
        mock_result.critical_issues = []
        mock_result.recommendations = []
        mock_result.go_live_conditions = []
        mock_result.summary = "test summary"
        mock_result.perspectives = []
        mock_result.recommendation = None
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.question = "test"
        mock_result.driver_bot_id = None
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"

        mock_expert_service.diagnose.return_value = mock_result

        service = GroupFusionService(
            provider=provider,
            expert_diagnosis_service=mock_expert_service,
        )

        request = FusionRequest(
            question="G5 test",
            participants=["anquan", "dba"],
            fusion_mode="expert_diagnosis",
        )

        result = service.fuse(request, group_id="grp-001")

        # ExpertDiagnosisService 应该被调用
        mock_expert_service.diagnose.assert_called_once()
        assert result.fusion_mode == "expert_diagnosis"


class TestG5PartialSuccess:
    """G5 partial success 测试"""

    def test_g5_partial_success_propagates(self):
        """测试 G5 partial_success 正确传播"""
        from src.application.services.group_fusion_service import GroupFusionService

        # 创建一个会有失败视角的 provider
        responses = {
            "anquan": Perspective(
                participant_id="anquan",
                participant_type="bot",
                role="expert",
                summary="安全视角完成",
                status="completed",
            ),
            "fawu": Perspective(
                participant_id="fawu",
                participant_type="bot",
                role="expert",
                summary="",
                status="failed",
            ),
        }

        provider = FakePerspectiveProvider(responses=responses)
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="上线评审",
            participants=["anquan", "fawu"],
            fusion_mode="expert_diagnosis",
        )

        result = service.fuse(request, group_id="grp-001")

        # partial_success 应该为 True
        assert result.partial_success is True
        # 应该有警告
        assert len(result.warnings) > 0


# =============================================================================
# G9: fusion_enable filtering Tests
# =============================================================================


class TestG9FusionEnableFiltering:
    """G9 模式 fusion_enable 检查（直接测 _check_fusion_enabled）"""

    def _make_service(self, worker_store=None):
        from src.application.services.group_fusion_service import GroupFusionService
        return GroupFusionService(provider=FakePerspectiveProvider(), worker_store=worker_store)

    def _make_store(self):
        from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
        return InMemoryWorkerRegistryStore()

    def _create_worker(self, store, worker_id: str, fusion_enable: bool = False):
        from src.domain.models.worker import (
            Worker, WorkerType, WorkerIdentity, WorkerState,
            Availability, TrustLevel, Capability, CapabilityLevel,
        )
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
        from src.domain.models.worker_config import WorkerConfig

        worker = Worker(
            id=worker_id,
            type=WorkerType.BOT,
            identity=WorkerIdentity(name=worker_id, handle=f"@{worker_id}"),
            responsibilities=["test"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PUBLIC, trust_level=TrustLevel.TRUSTED),
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            config=WorkerConfig(fusion_enable=fusion_enable),
        )
        store.create(worker)

    def test_fusion_enable_false_returns_disabled_ids(self):
        """fusion_enable=False 的 participant 被检出并产生 error"""
        store = self._make_store()
        self._create_worker(store, "wrk_a", fusion_enable=False)
        self._create_worker(store, "wrk_b", fusion_enable=True)

        svc = self._make_service(worker_store=store)
        errors: list[str] = []
        disabled = svc._check_fusion_enabled(["wrk_a", "wrk_b"], errors)

        assert disabled == ["wrk_a"]
        assert len(errors) > 0

    def test_all_enabled_passes(self):
        """所有 participant 都开启融合，通过检查"""
        store = self._make_store()
        self._create_worker(store, "wrk_a", fusion_enable=True)
        self._create_worker(store, "wrk_b", fusion_enable=True)

        svc = self._make_service(worker_store=store)
        errors: list[str] = []
        disabled = svc._check_fusion_enabled(["wrk_a", "wrk_b"], errors)

        assert disabled == []
        assert len(errors) == 0

    def test_unregistered_not_blocked(self):
        """未注册的 participant 不做 fusion_enable 检查"""
        store = self._make_store()
        self._create_worker(store, "wrk_a", fusion_enable=True)

        svc = self._make_service(worker_store=store)
        errors: list[str] = []
        disabled = svc._check_fusion_enabled(["wrk_a", "unknown"], errors)

        assert disabled == []
        assert len(errors) == 0

    def test_no_worker_store_skips_check(self):
        """未注入 worker_store 时跳过检查（向后兼容）"""
        svc = self._make_service(worker_store=None)
        errors: list[str] = []
        disabled = svc._check_fusion_enabled(["wrk_a", "wrk_b"], errors)

        assert disabled == []
        assert len(errors) == 0

    def test_all_disabled_detected(self):
        """所有已注册 participant 都关闭融合时全部检出"""
        store = self._make_store()
        self._create_worker(store, "wrk_a", fusion_enable=False)
        self._create_worker(store, "wrk_b", fusion_enable=False)

        svc = self._make_service(worker_store=store)
        errors: list[str] = []
        disabled = svc._check_fusion_enabled(["wrk_a", "wrk_b"], errors)

        assert set(disabled) == {"wrk_a", "wrk_b"}
        assert len(errors) > 0