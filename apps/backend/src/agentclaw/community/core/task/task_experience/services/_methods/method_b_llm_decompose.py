"""Method B placeholder — replaced by A5 with a real LLM-driven decompose.

Kept here so :class:`TaskPlanService.generate_task_plans` always has a
full four-method assemble + ranking path; A5 will overwrite this module's
implementation while preserving the public async entrypoint signature.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    PolicyRetrievalPort,
    TaskExperienceLLMProtocol,
)
from agentclaw.community.core.task.task_experience.services._methods._common import (
    _empty_plan,
)
from agentclaw.community.core.task.task_experience.types import Plan

__all__ = ["method_b_llm_decompose"]


async def method_b_llm_decompose(
    *,
    question: str,
    bot_id: str,
    llm: TaskExperienceLLMProtocol,
    policy: PolicyRetrievalPort,
    bot_tags: BotTagsPort,
) -> Plan:
    """Placeholder for A4 — replaced by A5 with LLM-driven decompose + backfill."""
    return _empty_plan("B", reason="not_implemented_in_a4")
