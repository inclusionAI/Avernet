"""Aggregate service producing the four candidate plans + a recommendation.

The four plan-generation methods (A / B / C / D) are consumed through
:class:`MethodAWorkflowPort` / :class:`MethodBDecomposePort` /
:class:`MethodCIncrementalPort` / :class:`MethodDSelfPlanPort` Protocols.
Community mode provides disabled placeholders that return empty plans;
real algorithm implementations live in the corp composition root.

The service itself only orchestrates the four ports and ranks their outputs
per ``-has_policy_support / -gain / total_duration`` — mirroring the
prototype's ``generate_task_plans`` recommendation rule.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    MethodAWorkflowPort,
    MethodBDecomposePort,
    MethodCIncrementalPort,
    MethodDSelfPlanPort,
    PolicyRetrievalPort,
    TaskExperienceLLMProtocol,
    TaskPlanServiceProtocol,
)
from agentclaw.community.core.task.task_experience.types import PlanReport

__all__ = ["TaskPlanService"]


class TaskPlanService(TaskPlanServiceProtocol):
    """Default :class:`TaskPlanServiceProtocol` implementation.

    Injects four method Ports + the shared LLM / policy / bot-tags ports;
    delegates plan generation to each method Port and ranks the results.
    """

    @inject
    def __init__(
        self,
        llm: TaskExperienceLLMProtocol,
        policy: PolicyRetrievalPort,
        bot_tags: BotTagsPort,
        method_a: MethodAWorkflowPort,
        method_b: MethodBDecomposePort,
        method_c: MethodCIncrementalPort,
        method_d: MethodDSelfPlanPort,
    ) -> None:
        self._llm = llm
        self._policy = policy
        self._bot_tags = bot_tags
        self._method_a = method_a
        self._method_b = method_b
        self._method_c = method_c
        self._method_d = method_d

    async def generate_task_plans(
        self,
        question: str,
        bot_id: str,
    ) -> PlanReport:
        plan_a = await self._method_a.generate(
            question=question, bot_id=bot_id,
            policy=self._policy, bot_tags=self._bot_tags,
        )
        plan_b = await self._method_b.generate(
            question=question, bot_id=bot_id,
            llm=self._llm, policy=self._policy, bot_tags=self._bot_tags,
        )
        plan_c = await self._method_c.generate(
            question=question, bot_id=bot_id,
            policy=self._policy, bot_tags=self._bot_tags,
        )
        plan_d = await self._method_d.generate(
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
