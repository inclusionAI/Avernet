"""
LLMSettings 测试

测试 LLM 配置加载和逻辑模型位注册。
"""

import os
import pytest
from unittest.mock import patch

from src.infra.llm.config.llm_settings import LLMSettings
from src.domain.models.model_profile import ModelProfile, ModelTier, CostClass, LatencyClass
from src.domain.models.llm_task_spec import TaskType


class TestLLMSettings:
    """LLMSettings 测试"""

    def test_default_settings(self):
        """测试默认配置"""
        with patch.dict(os.environ, {}, clear=True):
            settings = LLMSettings()

            assert settings.base_url is None
            assert settings.auth_token is None
            assert settings.default_timeout_ms == 15000
            assert settings.default_max_tokens == 4096
            assert settings.default_temperature == 0.2
            assert settings.enable_fallback is True
            assert settings.enable_retry is True
            assert settings.max_retries == 1

    def test_settings_from_env(self):
        """测试从环境变量加载配置"""
        env = {
            "LLM_BASE_URL": "https://api.example.com",
            "LLM_AUTH_TOKEN": "test-token-placeholder",
            "LLM_FAST_MODEL": "model-fast",
            "LLM_BALANCED_MODEL": "model-balanced",
            "LLM_REASONING_MODEL": "model-reasoning",
            "LLM_LONG_CONTEXT_MODEL": "model-long",
            "LLM_EXTRACTION_MODEL": "model-extract",
            "LLM_DEFAULT_TIMEOUT_MS": "30000",
            "LLM_DEFAULT_MAX_TOKENS": "4096",
            "LLM_DEFAULT_TEMPERATURE": "0.1",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = LLMSettings()

            assert settings.base_url == "https://api.example.com"
            assert settings.auth_token == "test-token-placeholder"
            assert settings.default_timeout_ms == 30000
            assert settings.default_max_tokens == 4096
            assert settings.default_temperature == 0.1

    def test_model_registry_loaded(self):
        """测试模型注册表加载"""
        with patch.dict(os.environ, {}, clear=True):
            settings = LLMSettings()

            # 默认应该有 5 个逻辑模型位
            assert len(settings.model_registry) == 5

            # 检查逻辑模型位存在
            profile_ids = [p.logical_model_id for p in settings.model_registry]
            assert "fast.default" in profile_ids
            assert "balanced.default" in profile_ids
            assert "reasoning.default" in profile_ids
            assert "long_context.default" in profile_ids
            assert "extraction.default" in profile_ids

    def test_get_model_profile(self):
        """测试获取模型档案"""
        with patch.dict(os.environ, {}, clear=True):
            settings = LLMSettings()

            # 获取存在的模型
            profile = settings.get_model_profile("fast.default")
            assert profile is not None
            assert profile.tier == ModelTier.FAST

            # 获取不存在的模型
            profile = settings.get_model_profile("nonexistent.model")
            assert profile is None

    def test_model_profile_structure(self):
        """测试模型档案结构"""
        env = {
            "LLM_FAST_MODEL": "fast-model-v1",
            "LLM_REASONING_MODEL": "reasoning-model-v1",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = LLMSettings()

            fast_profile = settings.get_model_profile("fast.default")
            assert fast_profile is not None
            assert fast_profile.physical_model_name == "fast-model-v1"
            assert fast_profile.tier == ModelTier.FAST
            assert fast_profile.cost_class == CostClass.LOW
            assert fast_profile.latency_class == LatencyClass.LOW

            reasoning_profile = settings.get_model_profile("reasoning.default")
            assert reasoning_profile is not None
            assert reasoning_profile.physical_model_name == "reasoning-model-v1"
            assert reasoning_profile.tier == ModelTier.REASONING
            assert reasoning_profile.cost_class == CostClass.HIGH
            assert reasoning_profile.latency_class == LatencyClass.HIGH

    def test_model_profile_recommended_for(self):
        """测试模型档案的推荐任务类型"""
        with patch.dict(os.environ, {}, clear=True):
            settings = LLMSettings()

            reasoning_profile = settings.get_model_profile("reasoning.default")
            assert reasoning_profile is not None
            assert TaskType.FUSION_RECOMMENDATION in reasoning_profile.recommended_for
            assert TaskType.PLANNING in reasoning_profile.recommended_for

            fast_profile = settings.get_model_profile("fast.default")
            assert fast_profile is not None
            assert TaskType.SUMMARY in fast_profile.recommended_for

    def test_no_real_token_in_code(self):
        """测试代码中没有硬编码的真实 token"""
        import inspect
        from src.infra.llm.config.llm_settings import LLMSettings

        source = inspect.getsource(LLMSettings)

        # 不应该包含常见的 token 模式
        assert "sk-" not in source
        assert "Bearer " not in source
        assert "anthropic" not in source.lower() or "provider_id" in source

    def test_get_timeout_for_task_type(self):
        """测试获取任务类型的超时配置"""
        env = {
            "LLM_REASONING_TIMEOUT_MS": "30000",
            "LLM_SUMMARY_TIMEOUT_MS": "8000",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = LLMSettings()

            # 默认超时
            assert settings.get_timeout_for_task(TaskType.EXTRACTION) == 15000

            # 推理任务超时（从环境变量）
            assert settings.get_timeout_for_task(TaskType.FUSION_RECOMMENDATION) == 30000
            assert settings.get_timeout_for_task(TaskType.PLANNING) == 30000

            # 摘要任务超时
            assert settings.get_timeout_for_task(TaskType.SUMMARY) == 8000