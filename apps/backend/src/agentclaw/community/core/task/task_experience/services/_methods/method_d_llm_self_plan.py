"""Method D placeholder — replaced by A5 with a real LLM self-plan.

Sibling stub to :mod:`method_b_llm_decompose`; same rationale: A4 ships a
full four-method TaskPlanService path; A5 swaps this stub for the real
LLM-driven self-plan.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    TaskExperienceLLMProtocol,
)
from agentclaw.community.core.task.task_experience.services._methods._common import (
    _empty_plan,
)
from agentclaw.community.core.task.task_experience.types import Plan

__all__ = ["method_d_llm_self_plan"]


async def method_d_llm_self_plan(
    *,
    question: str,
    bot_id: str,
    llm: TaskExperienceLLMProtocol,
    bot_tags: BotTagsPort,
) -> Plan:
    """Placeholder for A4 — replaced by A5 with LLM-driven self-plan."""
    return _empty_plan("D", reason="not_implemented_in_a4")
