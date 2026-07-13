"""
StaticLLMRouter 测试

测试静态任务路由器。
"""

import pytest

from src.infra.llm.routing.static_llm_router import StaticLLMRouter, ROUTING_RULES
from src.infra.llm.config.llm_settings import LLMSettings
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType
from src.domain.models.model_profile import ModelTier


class TestRoutingRules:
    """路由规则测试"""

    def test_routing_rules_defined(self):
        """测试路由规则已定义"""
        assert TaskType.FUSION_RECOMMENDATION in ROUTING_RULES
        assert TaskType.TASK_UNDERSTANDING in ROUTING_RULES
        assert TaskType.PLANNING in ROUTING_RULES
        assert TaskType.EXTRACTION in ROUTING_RULES
        assert TaskType.SUMMARY in ROUTING_RULES
        assert TaskType.RATIONALE_GENERATION in ROUTING_RULES

    def test_fusion_recommendation_routes_to_reasoning(self):
        """测试 FUSION_RECOMMENDATION 路由到 reasoning"""
        primary, fallback = ROUTING_RULES[TaskType.FUSION_RECOMMENDATION]
        assert primary == "reasoning.default"
        assert fallback == "balanced.default"

    def test_summary_routes_to_fast(self):
        """测试 SUMMARY 路由到 fast"""
        primary, fallback = ROUTING_RULES[TaskType.SUMMARY]
        assert primary == "fast.default"
        assert fallback == "balanced.default"


class TestStaticLLMRouter:
    """StaticLLMRouter 测试"""

    def test_route_fusion_recommendation(self):
        """测试路由 FUSION_RECOMMENDATION"""
        router = StaticLLMRouter()
        task_spec = LLMTaskSpec(task_type=TaskType.FUSION_RECOMMENDATION)

        profile = router.route(task_spec)

        assert profile.tier == ModelTier.REASONING
        assert profile.logical_model_id == "reasoning.default"

    def test_route_summary(self):
        """测试路由 SUMMARY"""
        router = StaticLLMRouter()
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)

        profile = router.route(task_spec)

        assert profile.tier == ModelTier.FAST
        assert profile.logical_model_id == "fast.default"

    def test_route_task_understanding(self):
        """测试路由 TASK_UNDERSTANDING"""
        router = StaticLLMRouter()
        task_spec = LLMTaskSpec(task_type=TaskType.TASK_UNDERSTANDING)

        profile = router.route(task_spec)

        assert profile.tier == ModelTier.BALANCED
        assert profile.logical_model_id == "balanced.default"

    def test_route_planning(self):
        """测试路由 PLANNING"""
        router = StaticLLMRouter()
        task_spec = LLMTaskSpec(task_type=TaskType.PLANNING)

        profile = router.route(task_spec)

        # PLANNING 可以路由到 reasoning 或 balanced
        assert profile.tier in (ModelTier.REASONING, ModelTier.BALANCED)

    def test_route_extraction(self):
        """测试路由 EXTRACTION"""
        router = StaticLLMRouter()
        task_spec = LLMTaskSpec(task_type=TaskType.EXTRACTION)

        profile = router.route(task_spec)

        assert profile.tier == ModelTier.EXTRACTION
        assert profile.logical_model_id == "extraction.default"

    def test_route_with_custom_settings(self):
        """测试使用自定义配置路由"""
        settings = LLMSettings(
            base_url="https://custom.api",
            default_timeout_ms=20000,
        )
        router = StaticLLMRouter(settings=settings)
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)

        profile = router.route(task_spec)

        assert profile is not None

    def test_route_with_complexity_high(self):
        """测试高复杂度任务路由"""
        router = StaticLLMRouter()
        task_spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            complexity="high",  # type: ignore
        )

        profile = router.route(task_spec)

        assert profile.tier == ModelTier.REASONING

    def test_route_with_structured_output_requirement(self):
        """测试需要结构化输出的任务路由"""
        router = StaticLLMRouter()
        task_spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            need_structured_output=True,
        )

        profile = router.route(task_spec)

        # 返回的模型应该支持 JSON 输出
        assert profile.supports_json is True

    def test_get_fallback_model(self):
        """测试获取 fallback 模型"""
        router = StaticLLMRouter()
        task_spec = LLMTaskSpec(task_type=TaskType.FUSION_RECOMMENDATION)

        fallback = router.get_fallback_model(task_spec)

        assert fallback is not None
        assert fallback.logical_model_id == "balanced.default"

    def test_get_fallback_model_none_for_some_tasks(self):
        """测试某些任务可能没有 fallback"""
        router = StaticLLMRouter()

        # SUMMARY 有 fallback
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        fallback = router.get_fallback_model(task_spec)
        assert fallback is not None

    def test_all_task_types_routable(self):
        """测试所有任务类型都可以路由"""
        router = StaticLLMRouter()

        for task_type in TaskType:
            task_spec = LLMTaskSpec(task_type=task_type)
            profile = router.route(task_spec)
            assert profile is not None, f"Task type {task_type} should be routable"