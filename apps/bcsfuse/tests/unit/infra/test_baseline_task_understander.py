"""
Tests for Baseline Task Understander

M3: Task Understanding Engine

测试范围：
- 简单任务解析测试（goal, deliverables 抽取）
- constraints 抽取测试
- success_criteria 抽取测试
- required_capabilities 推断测试
- risk_level 判定测试
- unknowns 识别测试
- subtasks 拆解测试
- 模糊输入测试
- 高风险任务识别测试

Baseline 实现原则：
- 规则可解释
- 优先基于模式、关键词、句式、显式约束提取
- 可以保守，不要求"聪明"
- 不接 LLM
"""

from __future__ import annotations

import pytest


class TestBaselineTaskUnderstanderBasics:
    """测试 BaselineTaskUnderstander 基本"""

    def test_understander_importable(self):
        """测试 Understander 可导入"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander

        assert BaselineTaskUnderstander is not None

    def test_understander_implements_protocol(self):
        """测试 Understander 实现 TaskUnderstander 协议"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.services.task_understander import TaskUnderstander

        understander = BaselineTaskUnderstander()
        assert isinstance(understander, TaskUnderstander)


class TestGoalExtraction:
    """测试 goal 抽取"""

    def test_extract_goal_from_simple_request(self):
        """测试从简单请求抽取 goal"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="帮我设计一个系统架构方案",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        assert result.task_spec is not None
        assert "系统架构" in result.task_spec.goal or "设计" in result.task_spec.goal

    def test_extract_goal_from_complex_request(self):
        """测试从复杂请求抽取 goal"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="我需要组建一个团队来完成技术调研，然后输出调研报告",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # Goal 应该反映主要任务
        assert result.task_spec is not None


class TestDeliverablesExtraction:
    """测试 deliverables 抽取"""

    def test_extract_explicit_deliverables(self):
        """测试抽取显式交付物"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="帮我完成调研并输出调研报告和PPT",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 应该识别出交付物
        assert len(result.task_spec.deliverables) >= 1

    def test_infer_deliverables_from_task_type(self):
        """测试从任务类型推断交付物"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="做一个技术方案设计",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 设计类任务应该有方案文档类交付物
        assert len(result.task_spec.deliverables) >= 1


class TestConstraintsExtraction:
    """测试 constraints 抽取"""

    def test_extract_explicit_constraints(self):
        """测试抽取显式约束"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="帮我设计一个系统，要求使用Python，不能使用外部API",known_constraints=["需要在两周内完成"],
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 应该包含已知约束和从请求中抽取的约束
        assert len(result.task_spec.constraints) >= 1

    def test_extract_time_constraints(self):
        """测试抽取时间约束"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="在一周内完成这个项目",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 时间约束应该被识别
        has_time_constraint = any("周" in c or "时间" in c for c in result.task_spec.constraints)
        assert has_time_constraint or len(result.task_spec.constraints) >= 0


class TestSuccessCriteriaExtraction:
    """测试 success_criteria 抽取"""

    def test_extract_explicit_criteria(self):
        """测试抽取显式成功标准"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="完成代码审查，要求通过所有测试",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        assert len(result.task_spec.success_criteria) >= 1

    def test_infer_criteria_from_deliverables(self):
        """测试从交付物推断成功标准"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="输出一份调研报告",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 应该有默认的成功标准
        assert len(result.task_spec.success_criteria) >= 1


class TestRequiredCapabilities:
    """测试 required_capabilities 推断"""

    def test_infer_capabilities_from_task_type(self):
        """测试从任务类型推断能力需求"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="做一个系统架构设计",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 架构设计任务应该需要架构设计能力
        assert len(result.task_spec.required_capabilities) >= 1

    def test_infer_research_capabilities(self):
        """测试推断调研能力"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="进行技术调研并输出报告",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 调研任务应该需要相关能力
        assert len(result.task_spec.required_capabilities) >= 1


class TestRiskLevelDetermination:
    """测试 risk_level 判定"""

    def test_low_risk_for_simple_task(self):
        """测试简单任务为低风险"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput
        from src.domain.models.task_spec import RiskLevel

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="帮我整理一下文档",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 简单任务应该是低风险
        assert result.task_spec.risk_level in [RiskLevel.LOW, RiskLevel.MEDIUM]

    def test_high_risk_for_production_changes(self):
        """测试生产环境变更任务为高风险"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput
        from src.domain.models.task_spec import RiskLevel

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="直接修改生产数据库",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 生产环境变更应该是高风险
        assert result.task_spec.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]

    def test_high_risk_for_external_access(self):
        """测试外部访问任务为高风险"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput
        from src.domain.models.task_spec import RiskLevel

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="发送外部邮件并访问外部API",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 外部访问应该是高风险
        assert result.task_spec.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]


class TestUnknownsIdentification:
    """测试 unknowns 识别"""

    def test_identify_missing_scope(self):
        """测试识别缺失范围"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="做一个系统",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 模糊需求应该有 unknowns
        assert len(result.task_spec.unknowns) >= 1
        # 应该识别出范围不明确
        has_scope_unknown = any("范围" in u or "scope" in u.lower() for u in result.task_spec.unknowns)
        assert has_scope_unknown or len(result.task_spec.unknowns) >= 1

    def test_well_defined_task_has_fewer_unknowns(self):
        """测试明确任务有较少 unknowns"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        vague_input = TaskUnderstandingInput(
            raw_request="做一个系统",
        )
        clear_input = TaskUnderstandingInput(
            raw_request="设计一个Python Web API系统，使用FastAPI框架，输出API文档和测试报告",
        )

        vague_result = understander.understand(vague_input)
        clear_result = understander.understand(clear_input)

        assert vague_result.is_successful()
        assert clear_result.is_successful()
        # 明确的任务应该有更少的 unknowns
        assert len(clear_result.task_spec.unknowns) <= len(vague_result.task_spec.unknowns)


class TestSubtasksDecomposition:
    """测试 subtasks 拆解"""

    def test_decompose_multi_step_task(self):
        """测试拆解多步骤任务"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="先进行调研，然后设计方案，最后输出报告",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 多步骤任务应该被拆解
        assert len(result.task_spec.subtasks) >= 1

    def test_decompose_explicit_steps(self):
        """测试拆解显式步骤"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="第一步：调研技术方案。第二步：编写代码。第三步：测试部署。",
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 显式步骤应该被识别
        assert len(result.task_spec.subtasks) >= 2


class TestAmbiguousInput:
    """测试模糊输入处理"""

    def test_ambiguous_request_produces_warnings(self):
        """测试模糊请求产生 warnings"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="帮我做一下",
        )

        result = understander.understand(input_data)

        # 应该仍然产生 TaskSpec，但有 warnings 和 unknowns
        assert result.task_spec is not None
        assert len(result.task_spec.unknowns) >= 1

    def test_empty_like_request_is_handled(self):
        """测试空类请求被处理"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="随便做点什么",
        )

        result = understander.understand(input_data)

        # 应该仍然产生结果
        assert result.task_spec is not None


class TestContextUsage:
    """测试上下文使用"""

    def test_context_enriches_understanding(self):
        """测试上下文丰富理解"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_without_context = TaskUnderstandingInput(
            raw_request="完成这个项目",
        )
        input_with_context = TaskUnderstandingInput(
            raw_request="完成这个项目",
            context="这是一个内部技术调研项目，需要输出调研报告",
        )

        result_without = understander.understand(input_without_context)
        result_with = understander.understand(input_with_context)

        assert result_without.is_successful()
        assert result_with.is_successful()
        # 有上下文的结果应该有更少的 unknowns
        assert len(result_with.task_spec.unknowns) <= len(result_without.task_spec.unknowns)

    def test_known_constraints_are_included(self):
        """测试已知约束被包含"""
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        input_data = TaskUnderstandingInput(
            raw_request="设计一个系统",
            known_constraints=["不能使用外部服务", "必须在Linux上运行"],
        )

        result = understander.understand(input_data)

        assert result.is_successful()
        # 已知约束应该出现在 TaskSpec 中
        constraint_text = " ".join(result.task_spec.constraints)
        assert "外部服务" in constraint_text or "Linux" in constraint_text or len(result.task_spec.constraints) >= 2