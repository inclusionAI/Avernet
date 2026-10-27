"""
FusionRecommendationPrompt

LLM Gateway / Provider Layer

G1 Fusion Recommendation 的 Prompt 模板构建。
"""

from __future__ import annotations

import json
from typing import Any, Optional


# System Prompt
SYSTEM_PROMPT = """你是一个"多参与者观点融合器"。

你的任务不是独立判断世界事实，而是基于输入的问题和多个参与者提供的观点，输出一个结构化 recommendation。

你必须遵守以下规则：

1. 只能基于输入的 question、perspectives、warnings、errors、partial_success 做判断。
2. 不得虚构任何 consultant 没有提供的事实。
3. 不得声称你访问了外部知识、数据库、网络或额外上下文。
4. 你的输出必须是严格 JSON，且必须符合给定 schema。
5. 你的 reasoning 字段只能写"可展示的理由摘要"，不要输出隐藏推理过程。
6. 如果信息不足，必须明确输出 `needs_more_information`，并在 `missing_information` 中说明。
7. 如果不同 perspectives 存在冲突，必须显式体现，不得强行编造统一结论。
8. confidence 应保守给出，不要盲目给高分。

【重要优化要求】：
9. 输出必须简洁有力，避免冗长的解释。
10. summary 限制在 100 字以内，直接给出结论。
11. reasoning/risks/next_actions 每个列表最多 5 条，每条不超过 50 字。
12. 优先输出最有价值的信息，而非面面俱到。
"""


# User Prompt 模板
USER_PROMPT_TEMPLATE = """请基于以下输入，输出 recommendation JSON。

[QUESTION]
{question}

[DRIVER]
{driver_bot_id}

[PARTIAL_SUCCESS]
{partial_success}

[WARNINGS]
{warnings_json}

[ERRORS]
{errors_json}

[PERSPECTIVES]
{perspectives_json}

【输出要求】请输出一个严格 JSON 对象，字段要求如下：
- summary: 80-100 字的建议摘要（直接给出结论）
- decision: 枚举值（yes/no/conditional_yes/needs_more_information）
- reasoning: 2-5 条理由，每条不超过 50 字
- risks: 2-5 条风险点，每条不超过 50 字
- missing_information: 缺失信息列表（如有）
- next_actions: 2-5 条行动建议，每条不超过 50 字
- confidence: 0-1 之间的浮点数

【关键约束】：
- 不要输出任何 JSON 之外的文字
- 不要虚构 consultant 未提供的事实
- 不要输出隐藏推理过程
- 务必简洁有力，避免冗余
- 总输出长度控制在 500 字以内
"""


# Output Schema
FUSION_RECOMMENDATION_SCHEMA = {
    "type": "object",
    "required": ["summary", "decision", "reasoning", "risks", "missing_information", "next_actions", "confidence"],
    "properties": {
        "summary": {"type": "string", "description": "建议摘要，面向用户可展示"},
        "decision": {
            "type": "string",
            "enum": ["yes", "no", "conditional_yes", "needs_more_information"],
            "description": "最终决策",
        },
        "reasoning": {
            "type": "array",
            "items": {"type": "string"},
            "description": "推理过程摘要，可展示的理由列表",
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "风险列表",
        },
        "missing_information": {
            "type": "array",
            "items": {"type": "string"},
            "description": "缺失信息列表",
        },
        "next_actions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "下一步行动建议",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "置信度",
        },
    },
}


class FusionRecommendationPrompt:
    """
    Fusion Recommendation Prompt 构建器

    负责构建 G1 fusion recommendation 的 system prompt 和 user prompt。
    """

    @staticmethod
    def get_system_prompt() -> str:
        """获取 System Prompt"""
        return SYSTEM_PROMPT

    @staticmethod
    def get_output_schema() -> dict[str, Any]:
        """获取输出 Schema"""
        return FUSION_RECOMMENDATION_SCHEMA.copy()

    @staticmethod
    def build_user_prompt(
        question: str,
        driver_bot_id: Optional[str],
        perspectives: list[dict[str, Any]],
        partial_success: bool = False,
        warnings: Optional[list[str]] = None,
        errors: Optional[list[str]] = None,
    ) -> str:
        """
        构建 User Prompt

        Args:
            question: 问题
            driver_bot_id: Driver bot ID
            perspectives: 视角列表
            partial_success: 是否部分成功
            warnings: 警告列表
            errors: 错误列表

        Returns:
            构建好的 User Prompt
        """
        return USER_PROMPT_TEMPLATE.format(
            question=question,
            driver_bot_id=driver_bot_id or "未指定",
            partial_success=str(partial_success).lower(),
            warnings_json=json.dumps(warnings or [], ensure_ascii=False, indent=2),
            errors_json=json.dumps(errors or [], ensure_ascii=False, indent=2),
            perspectives_json=json.dumps(perspectives, ensure_ascii=False, indent=2),
        )


def build_fusion_recommendation_prompt(
    question: str,
    driver_bot_id: Optional[str],
    perspectives: list[dict[str, Any]],
    partial_success: bool = False,
    warnings: Optional[list[str]] = None,
    errors: Optional[list[str]] = None,
) -> tuple[str, str]:
    """
    构建 Fusion Recommendation Prompt

    便捷函数，返回 (system_prompt, user_prompt)。

    Args:
        question: 问题
        driver_bot_id: Driver bot ID
        perspectives: 视角列表
        partial_success: 是否部分成功
        warnings: 警告列表
        errors: 错误列表

    Returns:
        tuple: (system_prompt, user_prompt)
    """
    system_prompt = FusionRecommendationPrompt.get_system_prompt()
    user_prompt = FusionRecommendationPrompt.build_user_prompt(
        question=question,
        driver_bot_id=driver_bot_id,
        perspectives=perspectives,
        partial_success=partial_success,
        warnings=warnings,
        errors=errors,
    )
    return system_prompt, user_prompt


__all__ = [
    "FusionRecommendationPrompt",
    "build_fusion_recommendation_prompt",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "FUSION_RECOMMENDATION_SCHEMA",
]