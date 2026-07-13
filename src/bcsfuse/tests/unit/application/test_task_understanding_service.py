"""
Tests for Task Understanding Service

M3: Task Understanding Engine

测试范围：
- 服务初始化测试
- 单一输入处理测试
- warnings/errors 聚合测试
- 上下文参与理解测试
- 异常处理测试

Service 职责：
- 接收输入
- 调用 understander
- 汇总 unknowns / warnings / errors
- 输出 TaskSpec 或 understanding result

Service 不做：
- 不解析自然语言（由 understander 做）
- 不实现抽取规则（由 understander 做）
"""

from __future__ import annotations

import pytest


class TestTaskUnderstandingServiceBasics:
    """测试 TaskUnderstandingService 基本"""

    def test_service_importable(self):
        """测试 Service 可导入"""
        from src.application.services.task_understanding_service import TaskUnderstandingService

        assert TaskUnderstandingService is not None

    def test_service_initializes_with_understander(self):
        """测试 Service 需要 understander 依赖"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)

        assert service is not None


class TestTaskUnderstandingServiceProcessing:
    """测试任务理解处理"""

    def test_process_simple_request(self):
        """测试处理简单请求"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)

        input_data = TaskUnderstandingInput(
            raw_request="帮我设计一个系统架构方案",
        )

        result = service.understand(input_data)

        assert result.is_successful()
        assert result.task_spec is not None
        assert result.task_spec.goal != ""

    def test_process_request_with_context(self):
        """测试处理带上下文的请求"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)

        input_data = TaskUnderstandingInput(
            raw_request="完成这个项目",
            context="这是一个内部技术调研项目，需要输出调研报告",
        )

        result = service.understand(input_data)

        assert result.is_successful()
        assert result.task_spec is not None

    def test_process_request_with_constraints(self):
        """测试处理带约束的请求"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)

        input_data = TaskUnderstandingInput(
            raw_request="设计一个系统",
            known_constraints=["不能使用外部服务", "必须在Linux上运行"],
        )

        result = service.understand(input_data)

        assert result.is_successful()
        # 约束应该被包含
        assert len(result.task_spec.constraints) >= 2


class TestTaskUnderstandingServiceWarnings:
    """测试 warnings 聚合"""

    def test_warnings_from_understander_preserved(self):
        """测试 understander 返回的 warnings 被保留"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)

        # 模糊请求应该产生 warnings
        input_data = TaskUnderstandingInput(
            raw_request="做一个系统",
        )

        result = service.understand(input_data)

        # 模糊请求应该有 unknowns
        assert len(result.task_spec.unknowns) >= 1


class TestTaskUnderstandingServiceErrors:
    """测试 errors 聚合"""

    def test_errors_from_understander_preserved(self):
        """测试 understander 返回的 errors 被保留"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.domain.models.task_understanding_input import TaskUnderstandingInput
        from src.domain.models.task_understanding_result import TaskUnderstandingResult

        # 创建一个返回 errors 的 mock understander
        class ErrorReturningUnderstander:
            def understand(self, input_data):
                result = TaskUnderstandingResult()
                from src.domain.models.task_understanding_result import UnderstandingError
                result.errors.append(UnderstandingError(
                    field="test",
                    message="Test error from understander",
                ))
                return result

        service = TaskUnderstandingService(understander=ErrorReturningUnderstander())

        input_data = TaskUnderstandingInput(raw_request="Test request")
        result = service.understand(input_data)

        assert not result.is_successful()
        assert len(result.errors) >= 1


class TestTaskUnderstandingServiceExceptionHandling:
    """测试异常处理"""

    def test_understander_exception_returns_error(self):
        """测试 understander 抛异常时返回 error"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.domain.models.task_understanding_input import TaskUnderstandingInput
        from src.domain.models.task_understanding_result import TaskUnderstandingResult

        # 创建一个会抛异常的 mock understander
        class BrokenUnderstander:
            def understand(self, input_data):
                raise RuntimeError("Simulated understanding failure")

        service = TaskUnderstandingService(understander=BrokenUnderstander())

        input_data = TaskUnderstandingInput(raw_request="Test request")
        result = service.understand(input_data)

        # Service 应该捕获异常并返回带有 error 的结果
        assert len(result.errors) >= 1
        assert any("failure" in e.message.lower() for e in result.errors)


class TestTaskUnderstandingServiceContextUsage:
    """测试上下文参与理解"""

    def test_context_reduces_unknowns(self):
        """测试上下文减少 unknowns"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)

        # 无上下文的请求
        input_without = TaskUnderstandingInput(
            raw_request="做一个系统",
        )

        # 有上下文的请求
        input_with = TaskUnderstandingInput(
            raw_request="做一个系统",
            context="这是一个Python Web API系统，使用FastAPI框架",
        )

        result_without = service.understand(input_without)
        result_with = service.understand(input_with)

        # 有上下文的结果应该有更少或相等的 unknowns
        assert len(result_with.task_spec.unknowns) <= len(result_without.task_spec.unknowns)

    def test_worker_hints_preserved(self):
        """测试 worker hints 被保留"""
        from src.application.services.task_understanding_service import TaskUnderstandingService
        from src.infra.understanders.baseline_task_understander import BaselineTaskUnderstander
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        understander = BaselineTaskUnderstander()
        service = TaskUnderstandingService(understander=understander)

        input_data = TaskUnderstandingInput(
            raw_request="完成调研任务",
            worker_hints=["bot_researcher_001", "bot_analyst_001"],
        )

        result = service.understand(input_data)

        assert result.is_successful()
        # worker hints 应该出现在资源需求中
        assert len(result.task_spec.required_resources) >= 1