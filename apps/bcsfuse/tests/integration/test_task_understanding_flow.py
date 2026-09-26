"""
Integration Tests for Task Understanding Flow

M3: Task Understanding Engine

验证完整的 task understanding 链路：
- 原始任务请求输入
- baseline task understander
- task understanding service
- TaskSpec 输出
- warnings / errors 保持正确

测试场景：
1. 完整闭环场景
2. 简单任务场景
3. 复杂任务场景
4. 模糊输入场景
5. 高风险任务场景
6. 异常传播场景
7. 上下文参与场景
"""

from __future__ import annotations

import pytest


class TestTaskUnderstandingFlowIntegration:
    """Task Understanding 流程集成测试"""

    # =========================================================================
    # 完整闭环场景
    # =========================================================================

    def test_complete_understanding_flow(self):
        """测试完整闭环：raw_request -> TaskSpec"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_complete_task_input

        input_data = get_complete_task_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        # 验证完整结果
        assert result.is_successful()
        assert result.task_spec is not None
        assert result.task_spec.goal != ""
        assert len(result.task_spec.deliverables) >= 1
        assert len(result.task_spec.constraints) >= 1
        assert len(result.task_spec.success_criteria) >= 1

    # =========================================================================
    # 简单任务场景
    # =========================================================================

    def test_simple_design_task(self):
        """测试简单设计任务"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_simple_design_input

        input_data = get_simple_design_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        assert result.task_spec is not None
        assert "设计" in result.task_spec.goal or "系统架构" in result.task_spec.goal
        # 设计类任务应该需要相关能力
        assert len(result.task_spec.required_capabilities) >= 1

    def test_simple_research_task(self):
        """测试简单调研任务"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_simple_research_input

        input_data = get_simple_research_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        assert result.task_spec is not None
        # 调研类任务应该有报告类交付物
        assert any("报告" in d for d in result.task_spec.deliverables)

    # =========================================================================
    # 复杂任务场景
    # =========================================================================

    def test_multi_step_task_decomposition(self):
        """测试多步骤任务拆解"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_multi_step_input

        input_data = get_multi_step_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        # 多步骤任务应该被拆解为子任务
        assert len(result.task_spec.subtasks) >= 2

    def test_task_with_constraints(self):
        """测试带约束的任务"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_input_with_constraints

        input_data = get_input_with_constraints()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        # 约束应该被抽取
        assert len(result.task_spec.constraints) >= 1

    # =========================================================================
    # 模糊输入场景
    # =========================================================================

    def test_vague_request_produces_unknowns(self):
        """测试模糊请求产生 unknowns"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_vague_input

        input_data = get_vague_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        # 应该仍然成功产生 TaskSpec
        assert result.is_successful()
        assert result.task_spec is not None
        # 但应该有 unknowns
        assert len(result.task_spec.unknowns) >= 1
        # 应该有 warning
        assert len(result.warnings) >= 1

    # =========================================================================
    # 高风险任务场景
    # =========================================================================

    def test_high_risk_production_task(self):
        """测试高风险生产环境任务"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_high_risk_production_input
        from src.domain.models.task_spec import RiskLevel

        input_data = get_high_risk_production_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        # 应该识别为高风险
        assert result.task_spec.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]

    def test_high_risk_external_task(self):
        """测试高风险外部访问任务"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_high_risk_external_input
        from src.domain.models.task_spec import RiskLevel

        input_data = get_high_risk_external_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        # 应该识别为高风险
        assert result.task_spec.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]

    # =========================================================================
    # 异常传播场景
    # =========================================================================

    def test_understander_exception_returns_structured_error(self):
        """测试 understander 抛异常时返回结构化错误"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.domain.models.task_understanding_input import TaskUnderstandingInput
        from src.domain.models.task_understanding_result import TaskUnderstandingResult

        # 创建会抛异常的 understander
        class BrokenUnderstander:
            def understand(self, input_data):
                raise RuntimeError("Simulated understanding failure")

        broken_understander = BrokenUnderstander()
        service = TaskUnderstandingService(understander=broken_understander)

        input_data = TaskUnderstandingInput(raw_request="Test request")
        result = service.understand(input_data)

        # 应该返回带 error 的结果，而不是抛异常
        assert not result.is_successful()
        assert len(result.errors) >= 1
        # 错误消息应该包含原始异常信息
        assert any("failure" in e.message.lower() for e in result.errors)

    # =========================================================================
    # 上下文参与场景
    # =========================================================================

    def test_context_enriches_understanding(self):
        """测试上下文丰富理解"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_input_with_context

        input_data = get_input_with_context()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        assert result.task_spec is not None

    def test_context_reduces_unknowns(self):
        """测试上下文减少 unknowns"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)

        # 无上下文
        input_without = TaskUnderstandingInput(raw_request="完成这个项目")
        # 有上下文
        input_with = TaskUnderstandingInput(
            raw_request="完成这个项目",
            context="这是一个Python Web API系统，使用FastAPI框架，需要输出API文档",
        )

        result_without = service.understand(input_without)
        result_with = service.understand(input_with)

        # 有上下文的结果应该有更少或相等的 unknowns
        assert len(result_with.task_spec.unknowns) <= len(result_without.task_spec.unknowns)

    # =========================================================================
    # Worker Hints 场景
    # =========================================================================

    def test_worker_hints_preserved(self):
        """测试 worker hints 被保留"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_input_with_worker_hints

        input_data = get_input_with_worker_hints()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        # worker hints 应该出现在资源需求中
        assert len(result.task_spec.required_resources) >= 1

    # =========================================================================
    # 明确任务场景
    # =========================================================================

    def test_well_defined_task_has_fewer_unknowns(self):
        """测试明确定义的任务有较少 unknowns"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_vague_input, get_clear_defined_input

        vague_input = get_vague_input()
        clear_input = get_clear_defined_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)

        vague_result = service.understand(vague_input)
        clear_result = service.understand(clear_input)

        # 明确的任务应该有更少的 unknowns
        assert len(clear_result.task_spec.unknowns) <= len(vague_result.task_spec.unknowns)

    # =========================================================================
    # Source Prompt 保留场景
    # =========================================================================

    def test_source_prompt_preserved(self):
        """测试原始请求被保留"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import REQUEST_SIMPLE_DESIGN
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        input_data = TaskUnderstandingInput(raw_request=REQUEST_SIMPLE_DESIGN)

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        # source_prompt 应该被保留
        assert result.source_prompt == REQUEST_SIMPLE_DESIGN
        assert result.task_spec.source_prompt == REQUEST_SIMPLE_DESIGN

    # =========================================================================
    # 成功标准推断场景
    # =========================================================================

    def test_success_criteria_inferred_from_deliverables(self):
        """测试从交付物推断成功标准"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_simple_research_input

        input_data = get_simple_research_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        # 应该有成功标准
        assert len(result.task_spec.success_criteria) >= 1
        # 成功标准应该包含交付物完成
        criteria_text = " ".join(result.task_spec.success_criteria)
        assert "完成" in criteria_text or "已完成" in criteria_text

    # =========================================================================
    # 能力推断场景
    # =========================================================================

    def test_capabilities_inferred_from_task_type(self):
        """测试从任务类型推断能力"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_simple_design_input

        input_data = get_simple_design_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        assert result.is_successful()
        # 设计类任务应该推断出相关能力
        capabilities_text = " ".join(result.task_spec.required_capabilities)
        assert "设计" in capabilities_text or "架构" in capabilities_text or len(result.task_spec.required_capabilities) >= 1

    # =========================================================================
    # Warnings 聚合场景
    # =========================================================================

    def test_warnings_aggregated_for_unknowns(self):
        """测试 unknowns 产生 warnings"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from tests.fixtures.task_requests import get_vague_input

        input_data = get_vague_input()

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)
        result = service.understand(input_data)

        # 模糊请求应该有 unknowns
        if len(result.task_spec.unknowns) > 0:
            # 应该产生 warning
            assert len(result.warnings) >= 1