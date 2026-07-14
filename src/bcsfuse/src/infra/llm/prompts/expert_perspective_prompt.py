"""
ExpertPerspectivePrompt

Stage 3: Worker Profile-Driven Expert Execution Preparation

G5 Expert Perspective 的 Prompt 模板构建。
"""

from __future__ import annotations

import json
from typing import Any, Optional


# System Prompt
SYSTEM_PROMPT = """你是一个"领域专家视角生成器"。

你的任务是基于专家画像和问题，生成一个专家视角。

你必须遵守以下规则：

1. 只能基于输入的专家画像（expertise_summary, relevant_skills, context_highlights）生成视角。
2. 不得虚构专家未提及的技能或经验。
3. 不得声称你访问了外部知识、数据库、网络或额外上下文。
4. 你的输出必须是严格 JSON，且必须符合给定 schema。
5. rationale_summary 字段只能写"依据摘要"，不要输出隐藏推理过程。
6. 如果信息不足以形成专家视角，应该降低 confidence 并在 concerns 中说明。
7. confidence 应保守给出，不要盲目给高分。
8. risk_level 应基于 concerns 的严重程度给出。"""


# User Prompt 模板
USER_PROMPT_TEMPLATE = """请基于以下专家画像，生成专家视角 JSON。

[QUESTION]
{question}

[EXPERT_ID]
{expert_id}

[DOMAIN]
{domain}

[EXPERTISE_SUMMARY]
{expertise_summary}

[RELEVANT_SKILLS]
{skills_json}

[CONTEXT_HIGHLIGHTS]
{highlights_json}

[TASK_CONTEXT]
{task_context}

请输出一个严格 JSON 对象，字段必须完全符合以下要求：
- summary: 字符串，专家视角摘要
- confidence: 0-1 之间的浮点数，置信度
- key_points: 字符串列表，核心观点
- concerns: 字符串列表，主要顾虑
- risk_level: 枚举值（low/medium/high/critical）
- rationale_summary: 字符串，依据摘要（不是推理过程）
- evidence_summary: 字符串列表，证据摘要列表

再次强调：
- 不要输出任何 JSON 之外的文字
- 不要虚构专家未提及的事实
- rationale_summary 是依据摘要，不是推理过程"""


# Output Schema
EXPERT_PERSPECTIVE_SCHEMA = {
    "type": "object",
    "required": ["summary", "confidence", "key_points", "concerns", "risk_level", "rationale_summary", "evidence_summary"],
    "properties": {
        "summary": {
            "type": "string",
            "description": "专家视角摘要",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "置信度",
        },
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "核心观点列表",
        },
        "concerns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "主要顾虑列表",
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
            "description": "风险等级",
        },
        "rationale_summary": {
            "type": "string",
            "description": "依据摘要（不是推理过程）",
        },
        "evidence_summary": {
            "type": "array",
            "items": {"type": "string"},
            "description": "证据摘要列表",
        },
    },
}


class ExpertPerspectivePrompt:
    """
    Expert Perspective Prompt 构建器

    负责构建 G5 expert perspective 的 system prompt 和 user prompt。
    """

    @staticmethod
    def get_system_prompt() -> str:
        """获取 System Prompt"""
        return SYSTEM_PROMPT

    @staticmethod
    def get_output_schema() -> dict[str, Any]:
        """获取输出 Schema"""
        return EXPERT_PERSPECTIVE_SCHEMA.copy()

    @staticmethod
    def build_user_prompt(
        question: str,
        expert_id: str,
        domain: str,
        expertise_summary: str,
        relevant_skills: list[str],
        context_highlights: list[str],
        task_context: str,
    ) -> str:
        """
        构建 User Prompt

        Args:
            question: 问题
            expert_id: 专家标识
            domain: 领域
            expertise_summary: 专长摘要
            relevant_skills: 相关技能列表
            context_highlights: 上下文要点列表
            task_context: 任务上下文

        Returns:
            构建好的 User Prompt
        """
        return USER_PROMPT_TEMPLATE.format(
            question=question,
            expert_id=expert_id,
            domain=domain,
            expertise_summary=expertise_summary,
            skills_json=json.dumps(relevant_skills, ensure_ascii=False, indent=2),
            highlights_json=json.dumps(context_highlights, ensure_ascii=False, indent=2),
            task_context=task_context or "无特定任务上下文",
        )


def build_expert_perspective_prompt(
    question: str,
    expert_id: str,
    domain: str,
    expertise_summary: str,
    relevant_skills: list[str],
    context_highlights: list[str],
    task_context: str,
) -> tuple[str, str]:
    """
    构建 Expert Perspective Prompt

    便捷函数，返回 (system_prompt, user_prompt)。

    Args:
        question: 问题
        expert_id: 专家标识
        domain: 领域
        expertise_summary: 专长摘要
        relevant_skills: 相关技能列表
        context_highlights: 上下文要点列表
        task_context: 任务上下文

    Returns:
        tuple: (system_prompt, user_prompt)
    """
    system_prompt = ExpertPerspectivePrompt.get_system_prompt()
    user_prompt = ExpertPerspectivePrompt.build_user_prompt(
        question=question,
        expert_id=expert_id,
        domain=domain,
        expertise_summary=expertise_summary,
        relevant_skills=relevant_skills,
        context_highlights=context_highlights,
        task_context=task_context,
    )
    return system_prompt, user_prompt


__all__ = [
    "ExpertPerspectivePrompt",
    "build_expert_perspective_prompt",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "EXPERT_PERSPECTIVE_SCHEMA",
]