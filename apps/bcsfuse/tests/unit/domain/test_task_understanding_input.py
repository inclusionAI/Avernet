"""
Tests for Task Understanding Input

M3: Task Understanding Engine

测试范围：
- TaskUnderstandingInput 创建与字段校验
- 空输入/无效输入测试
-可选字段处理

输入模型职责：
- 接收用户原始任务描述
- 支持可选的补充上下文
- 支持可选的已知约束
- 支持可选的已有 Worker / profiling 结果摘要
"""

from __future__ import annotations

import pytest


class TestTaskUnderstandingInputBasics:
    """测试 TaskUnderstandingInput 基本"""

    def test_input_importable(self):
        """测试 Input 可导入"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        assert TaskUnderstandingInput is not None

    def test_create_input_with_minimal_fields(self):
        """测试最小字段创建输入"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        input_data = TaskUnderstandingInput(
            raw_request="帮我做一个系统方案",
        )

        assert input_data.raw_request == "帮我做一个系统方案"
        assert input_data.context is None
        assert input_data.known_constraints == []

    def test_create_input_with_all_fields(self):
        """测试所有字段创建输入"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        input_data = TaskUnderstandingInput(
            raw_request="帮我组一个团队完成调研任务",
            context="这是一个内部技术调研项目",
            known_constraints=["不能使用外部资源", "需要在两周内完成"],
            worker_hints=["worker_researcher_001"],
            metadata={"priority": "high", "department": "tech"},
        )

        assert input_data.raw_request == "帮我组一个团队完成调研任务"
        assert input_data.context == "这是一个内部技术调研项目"
        assert len(input_data.known_constraints) == 2
        assert len(input_data.worker_hints) == 1
        assert input_data.metadata["priority"] == "high"


class TestTaskUnderstandingInputValidation:
    """测试 TaskUnderstandingInput 校验"""

    def test_empty_raw_request_raises_error(self):
        """测试空请求抛出错误"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TaskUnderstandingInput(raw_request="")

    def test_whitespace_only_request_raises_error(self):
        """测试只有空白字符抛出错误"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TaskUnderstandingInput(raw_request="   \n\n   ")

    def test_request_too_long_raises_error(self):
        """测试过长请求抛出错误"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput
        from pydantic import ValidationError

        long_request = "a" * 10001  # 超过默认 10000 字符限制

        with pytest.raises(ValidationError):
            TaskUnderstandingInput(raw_request=long_request)

    def test_request_at_max_length_succeeds(self):
        """测试最大长度请求成功"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        max_request = "a" * 10000  # 正好10000 字符

        input_data = TaskUnderstandingInput(raw_request=max_request)
        assert len(input_data.raw_request) == 10000


class TestTaskUnderstandingInputOptionalFields:
    """测试可选字段处理"""

    def test_context_is_optional(self):
        """测试context 可选"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        input_data = TaskUnderstandingInput(raw_request="Test task")

        assert input_data.context is None

    def test_known_constraints_defaults_to_empty_list(self):
        """测试 known_constraints 默认空列表"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        input_data = TaskUnderstandingInput(raw_request="Test task")

        assert input_data.known_constraints == []

    def test_worker_hints_defaults_to_empty_list(self):
        """测试 worker_hints 默认空列表"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        input_data = TaskUnderstandingInput(raw_request="Test task")

        assert input_data.worker_hints == []

    def test_metadata_defaults_to_empty_dict(self):
        """测试 metadata 默认空字典"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        input_data = TaskUnderstandingInput(raw_request="Test task")

        assert input_data.metadata == {}


class TestTaskUnderstandingInputWorkerHints:
    """测试 worker_hints 字段"""

    def test_worker_hints_accepts_valid_worker_ids(self):
        """测试 worker_hints 接受有效 worker ID"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        input_data = TaskUnderstandingInput(
            raw_request="Test task",
            worker_hints=["bot_researcher_001", "human_reviewer_001"],
        )

        assert len(input_data.worker_hints) == 2

    def test_worker_hints_empty_list_is_valid(self):
        """测试空 worker_hints 列表有效"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        input_data = TaskUnderstandingInput(
            raw_request="Test task",
            worker_hints=[],
        )

        assert input_data.worker_hints == []


class TestTaskUnderstandingInputConstraints:
    """测试 known_constraints 字段"""

    def test_constraints_are_preserved(self):
        """测试约束被保留"""
        from src.domain.models.task_understanding_input import TaskUnderstandingInput

        input_data = TaskUnderstandingInput(
            raw_request="Test task",
            known_constraints=[
                "禁止访问生产环境",
                "必须经过审批",
            ],
        )

        assert "禁止访问生产环境" in input_data.known_constraints
        assert "必须经过审批" in input_data.known_constraints