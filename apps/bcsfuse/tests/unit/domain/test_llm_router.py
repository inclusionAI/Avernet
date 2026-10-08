"""
LLMRouter 协议测试

测试 LLM Router 协议的定义和行为。
"""

import pytest
from typing import Protocol

from src.domain.services.llm_router import LLMRouter
from src.domain.models.model_profile import ModelProfile, ModelTier
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType


class TestLLMRouterProtocol:
    """LLMRouter 协议测试"""

    def test_is_protocol(self):
        """测试 LLMRouter 是 Protocol"""
        assert issubclass(LLMRouter, Protocol)

    def test_protocol_has_route_method(self):
        """测试协议有 route 方法"""
        assert hasattr(LLMRouter, 'route')

    def test_concrete_implementation(self):
        """测试具体实现满足协议"""
        class FakeRouter:
            """测试用的 fake router"""

            def route(self, task_spec: LLMTaskSpec) -> ModelProfile:
                return ModelProfile(
                    logical_model_id="fast.default",
                    provider_id="fake",
                    physical_model_name="fake-model",
                    tier=ModelTier.FAST,
                )

        router = FakeRouter()

        assert isinstance(router, LLMRouter)

    def test_route_signature(self):
        """测试 route 方法签名"""
        class FakeRouter:
            def route(self, task_spec: LLMTaskSpec) -> ModelProfile:
                tier_map = {
                    TaskType.SUMMARY: ModelTier.FAST,
                    TaskType.FUSION_RECOMMENDATION: ModelTier.REASONING,
                }
                tier = tier_map.get(task_spec.task_type, ModelTier.BALANCED)
                return ModelProfile(
                    logical_model_id=f"{tier.value}.default",
                    provider_id="fake",
                    physical_model_name="fake-model",
                    tier=tier,
                )

        router = FakeRouter()

        # 测试 SUMMARY -> FAST
        spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        profile = router.route(spec)
        assert profile.tier == ModelTier.FAST

        # 测试 FUSION_RECOMMENDATION -> REASONING
        spec = LLMTaskSpec(task_type=TaskType.FUSION_RECOMMENDATION)
        profile = router.route(spec)
        assert profile.tier == ModelTier.REASONING


class TestLLMRouterIntegration:
    """LLMRouter 协议集成测试"""

    def test_router_can_be_used_polymorphically(self):
        """测试 router 可以多态使用"""
        class RouterA:
            def route(self, task_spec: LLMTaskSpec) -> ModelProfile:
                return ModelProfile(
                    logical_model_id="router-a-model",
                    provider_id="provider-a",
                    physical_model_name="model-a",
                    tier=ModelTier.BALANCED,
                )

        class RouterB:
            def route(self, task_spec: LLMTaskSpec) -> ModelProfile:
                return ModelProfile(
                    logical_model_id="router-b-model",
                    provider_id="provider-b",
                    physical_model_name="model-b",
                    tier=ModelTier.REASONING,
                )

        def use_router(router: LLMRouter, task_spec: LLMTaskSpec) -> str:
            profile = router.route(task_spec)
            return profile.logical_model_id

        spec = LLMTaskSpec(task_type=TaskType.PLANNING)

        result_a = use_router(RouterA(), spec)
        result_b = use_router(RouterB(), spec)

        assert result_a == "router-a-model"
        assert result_b == "router-b-model"