"""Aggregate service producing the four candidate plans + a recommendation.

Methods A and C run on :class:`PolicyRetrievalPort` (KGBase); methods B and D
run on :class:`TaskExperienceLLMProtocol` (LLM-driven, still populated in A5).
The service only orchestrates per-method generators and ranks their outputs
per ``-has_policy_support / -gain / total_duration`` — mirroring the
prototype's ``generate_task_plans`` recommendation rule.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    PolicyRetrievalPort,
    TaskExperienceLLMProtocol,
    TaskPlanServiceProtocol,
)
from agentclaw.community.core.task.task_experience.services._methods.method_a_workflow import (
    method_a_workflow,
)
from agentclaw.community.core.task.task_experience.services._methods.method_b_llm_decompose import (
    method_b_llm_decompose,
)
from agentclaw.community.core.task.task_experience.services._methods.method_c_incremental import (
    method_c_incremental,
)
from agentclaw.community.core.task.task_experience.services._methods.method_d_llm_self_plan import (
    method_d_llm_self_plan,
)
from agentclaw.community.core.task.task_experience.types import PlanReport

__all__ = ["TaskPlanService"]


class TaskPlanService(TaskPlanServiceProtocol):
    """Default :class:`TaskPlanServiceProtocol` implementation."""

    @inject
    def __init__(
        self,
        llm: TaskExperienceLLMProtocol,
        policy: PolicyRetrievalPort,
        bot_tags: BotTagsPort,
    ) -> None:
        self._llm = llm
        self._policy = policy
        self._bot_tags = bot_tags

    async def generate_task_plans(
        self,
        question: str,
        bot_id: str,
    ) -> PlanReport:
        plan_a = await method_a_workflow(
            question=question, bot_id=bot_id,
            policy=self._policy, bot_tags=self._bot_tags,
        )
        plan_b = await method_b_llm_decompose(
            question=question, bot_id=bot_id,
            llm=self._llm, policy=self._policy, bot_tags=self._bot_tags,
        )
        plan_c = await method_c_incremental(
            question=question, bot_id=bot_id,
            policy=self._policy, bot_tags=self._bot_tags,
        )
        plan_d = await method_d_llm_self_plan(
            question=question, bot_id=bot_id,
            llm=self._llm, bot_tags=self._bot_tags,
        )
        plans = [plan_a, plan_b, plan_c, plan_d]
        ranked = sorted(
            plans,
            key=lambda p: (-p.has_policy_support, -p.gain, p.total_duration),
        )
        recommendation = ranked[0].method if ranked else "D"
        return PlanReport(
            question=question,
            bot_id=bot_id,
            plans=tuple(plans),
            recommendation=recommendation,
        )
