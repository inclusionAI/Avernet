"""
LLMRequest 领域模型测试

测试 LLM 请求模型的验证和行为。
"""

import pytest
from pydantic import ValidationError

from src.domain.models.llm_request import LLMRequest, LLMRequestMetadata
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType


class TestLLMRequestMetadata:
    """LLMRequestMetadata 测试"""

    def test_create_minimal_metadata(self):
        """测试创建最小元数据"""
        metadata = LLMRequestMetadata()
        assert metadata.trace_id is None
        assert metadata.session_id is None
        assert metadata.source is None

    def test_create_full_metadata(self):
        """测试创建完整元数据"""
        metadata = LLMRequestMetadata(
            trace_id="trace-123",
            session_id="session-456",
            source="fusion_recommendation_service",
        )

        assert metadata.trace_id == "trace-123"
        assert metadata.session_id == "session-456"
        assert metadata.source == "fusion_recommendation_service"

    def test_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(ValidationError):
            LLMRequestMetadata(unknown_field="value")  # type: ignore


class TestLLMRequest:
    """LLMRequest 模型测试"""

    def test_create_minimal_request(self):
        """测试创建最小请求"""
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="请总结以下内容",
        )

        assert request.task_spec == task_spec
        assert request.user_prompt == "请总结以下内容"
        assert request.system_prompt is None
        assert request.temperature == 0.2  # 默认值
        assert request.max_tokens == 4096  # 默认值
        assert request.expected_schema_name is None
        assert request.metadata is None

    def test_create_full_request(self):
        """测试创建完整请求"""
        task_spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            need_structured_output=True,
        )
        metadata = LLMRequestMetadata(trace_id="trace-123")

        request = LLMRequest(
            task_spec=task_spec,
            system_prompt="你是一个多参与者观点融合器。",
            user_prompt="请基于以下视角输出建议...",
            temperature=0.1,
            max_tokens=4096,
            expected_schema_name="FusionRecommendation",
            metadata=metadata,
        )

        assert request.task_spec.task_type == TaskType.FUSION_RECOMMENDATION
        assert request.system_prompt == "你是一个多参与者观点融合器。"
        assert request.user_prompt == "请基于以下视角输出建议..."
        assert request.temperature == 0.1
        assert request.max_tokens == 4096
        assert request.expected_schema_name == "FusionRecommendation"
        assert request.metadata.trace_id == "trace-123"

    def test_required_fields(self):
        """测试必填字段"""
        with pytest.raises(ValidationError) as exc_info:
            LLMRequest()

        errors = exc_info.value.errors()
        error_fields = {e["loc"][0] for e in errors}
        assert "task_spec" in error_fields
        assert "user_prompt" in error_fields

    def test_temperature_range(self):
        """测试 temperature 范围验证"""
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)

        # 有效范围
        for temp in [0.0, 0.5, 1.0, 1.5, 2.0]:
            request = LLMRequest(
                task_spec=task_spec,
                user_prompt="test",
                temperature=temp,
            )
            assert request.temperature == temp

        # 超出范围
        with pytest.raises(ValidationError):
            LLMRequest(
                task_spec=task_spec,
                user_prompt="test",
                temperature=-0.1,
            )

        with pytest.raises(ValidationError):
            LLMRequest(
                task_spec=task_spec,
                user_prompt="test",
                temperature=2.1,
            )

    def test_max_tokens_range(self):
        """测试 max_tokens 范围验证"""
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)

        # 有效范围
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
            max_tokens=1,
        )
        assert request.max_tokens == 1

        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
            max_tokens=128000,
        )
        assert request.max_tokens == 128000

        # 超出范围
        with pytest.raises(ValidationError):
            LLMRequest(
                task_spec=task_spec,
                user_prompt="test",
                max_tokens=0,
            )

        with pytest.raises(ValidationError):
            LLMRequest(
                task_spec=task_spec,
                user_prompt="test",
                max_tokens=200000,
            )

    def test_user_prompt_not_empty(self):
        """测试 user_prompt 不能为空"""
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)

        with pytest.raises(ValidationError):
            LLMRequest(
                task_spec=task_spec,
                user_prompt="",
            )

    def test_model_dump(self):
        """测试模型序列化"""
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test prompt",
            temperature=0.5,
        )

        data = request.model_dump()

        assert "task_spec" in data
        assert data["user_prompt"] == "test prompt"
        assert data["temperature"] == 0.5