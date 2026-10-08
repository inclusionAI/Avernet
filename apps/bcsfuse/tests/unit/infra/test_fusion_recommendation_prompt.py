"""
FusionRecommendationPrompt 测试

测试 Fusion Recommendation Prompt 模板构建。
"""

import pytest

from src.infra.llm.prompts.fusion_recommendation_prompt import (
    FusionRecommendationPrompt,
    build_fusion_recommendation_prompt,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    FUSION_RECOMMENDATION_SCHEMA,
)


class TestFusionRecommendationPrompt:
    """FusionRecommendationPrompt 测试"""

    def test_get_system_prompt(self):
        """测试获取 System Prompt"""
        prompt = FusionRecommendationPrompt.get_system_prompt()

        assert len(prompt) > 0
        assert "多参与者观点融合器" in prompt
        assert "JSON" in prompt

    def test_system_prompt_contains_rules(self):
        """测试 System Prompt 包含关键规则"""
        prompt = FusionRecommendationPrompt.get_system_prompt()

        # 应该包含核心规则
        assert "不得虚构" in prompt
        assert "只能基于输入" in prompt
        assert "confidence" in prompt.lower()
        assert "needs_more_information" in prompt

    def test_get_output_schema(self):
        """测试获取输出 Schema"""
        schema = FusionRecommendationPrompt.get_output_schema()

        assert schema["type"] == "object"
        assert "summary" in schema["required"]
        assert "decision" in schema["required"]
        assert "confidence" in schema["required"]

    def test_schema_decision_enum(self):
        """测试 Schema 中的 decision 枚举"""
        schema = FusionRecommendationPrompt.get_output_schema()

        decision_enum = schema["properties"]["decision"]["enum"]
        assert "yes" in decision_enum
        assert "no" in decision_enum
        assert "conditional_yes" in decision_enum
        assert "needs_more_information" in decision_enum

    def test_build_user_prompt_minimal(self):
        """测试构建最小 User Prompt"""
        prompt = FusionRecommendationPrompt.build_user_prompt(
            question="这个方案是否可行?",
            driver_bot_id="bot-001",
            perspectives=[],
        )

        assert "这个方案是否可行?" in prompt
        assert "bot-001" in prompt

    def test_build_user_prompt_with_perspectives(self):
        """测试构建带视角的 User Prompt"""
        perspectives = [
            {
                "participant_id": "dba",
                "summary": "从数据库角度可行",
                "confidence": 0.85,
                "status": "completed",
            },
            {
                "participant_id": "security",
                "summary": "需要补充审计",
                "confidence": 0.7,
                "status": "completed",
            },
        ]

        prompt = FusionRecommendationPrompt.build_user_prompt(
            question="测试问题",
            driver_bot_id="test-driver",
            perspectives=perspectives,
        )

        assert "dba" in prompt
        assert "security" in prompt
        assert "从数据库角度可行" in prompt

    def test_build_user_prompt_with_warnings_errors(self):
        """测试构建带警告和错误的 User Prompt"""
        prompt = FusionRecommendationPrompt.build_user_prompt(
            question="测试",
            driver_bot_id="test",
            perspectives=[],
            partial_success=True,
            warnings=["participant timeout"],
            errors=["connection failed"],
        )

        assert "true" in prompt
        assert "participant timeout" in prompt
        assert "connection failed" in prompt

    def test_build_user_prompt_no_driver(self):
        """测试没有 driver 时的处理"""
        prompt = FusionRecommendationPrompt.build_user_prompt(
            question="测试",
            driver_bot_id=None,
            perspectives=[],
        )

        assert "未指定" in prompt


class TestBuildFusionRecommendationPrompt:
    """build_fusion_recommendation_prompt 函数测试"""

    def test_returns_tuple(self):
        """测试返回元组"""
        result = build_fusion_recommendation_prompt(
            question="test",
            driver_bot_id="test",
            perspectives=[],
        )

        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_returns_system_and_user_prompts(self):
        """测试返回 system 和 user prompts"""
        system_prompt, user_prompt = build_fusion_recommendation_prompt(
            question="test question",
            driver_bot_id="driver-001",
            perspectives=[{"id": "test"}],
        )

        assert "多参与者观点融合器" in system_prompt
        assert "test question" in user_prompt
        assert "driver-001" in user_prompt


class TestPromptConstants:
    """Prompt 常量测试"""

    def test_system_prompt_constant(self):
        """测试 SYSTEM_PROMPT 常量"""
        assert SYSTEM_PROMPT == FusionRecommendationPrompt.get_system_prompt()

    def test_user_prompt_template_constant(self):
        """测试 USER_PROMPT_TEMPLATE 常量"""
        assert "{question}" in USER_PROMPT_TEMPLATE
        assert "{perspectives_json}" in USER_PROMPT_TEMPLATE

    def test_schema_constant(self):
        """测试 FUSION_RECOMMENDATION_SCHEMA 常量"""
        assert FUSION_RECOMMENDATION_SCHEMA == FusionRecommendationPrompt.get_output_schema()