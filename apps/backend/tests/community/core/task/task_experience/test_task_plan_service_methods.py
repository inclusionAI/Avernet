"""Unit tests for TaskPlanService (method A/C + ranking) with stub Protocols.

Covers the A4 acceptance points:
  (a) disabled / empty KGBase retrieval ⇒ method A returns an empty plan with
      ``reason`` set;
  (b) full retrieval returns a plan with ``has_policy_support=True``;
  (c) generate_task_plans recommendation picks the earliest policy-backed plan.
Stubs implement the runtime_checkable Protocols; no network calls.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    PolicyRetrievalPort,
    TaskExperienceLLMProtocol,
)
from agentclaw.community.core.task.task_experience.services._methods.method_a_workflow import (
    method_a_workflow,
)
from agentclaw.community.core.task.task_experience.services._methods.method_c_incremental import (
    method_c_incremental,
)
from agentclaw.community.core.task.task_experience.services.task_plan_service import (
    TaskPlanService,
)
from agentclaw.community.core.task.task_experience.types import (
    KGBasePolicy,
    PlanReport,
    TASK_EXPERIENCE_LLM_DISABLED,
)


class _StubBotTags(BotTagsPort):
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self._mapping = mapping or {}

    def bot_name(self, bot_id: str) -> str:
        return self._mapping.get(bot_id, bot_id)


class _StubPolicyRetrieval(PolicyRetrievalPort):
    """Returns fixtures keyed by the ``experience_type`` arg.

    Each retrieve call stores its arguments for assertions.
    """

    def __init__(self, fixtures: dict[str, list[KGBasePolicy]] | None = None) -> None:
        self._fixtures = fixtures or {}
        self.calls: list[dict[str, Any]] = []

    async def retrieve(
        self,
        query: str,
        *,
        bot_id: str,
        entity_type: str = "Policy",
        property_name: str = "trigger",
        top_k: int = 5,
        min_score: float = 0.3,
        experience_type: str | None = None,
        active_only: bool = True,
    ) -> list[KGBasePolicy]:
        self.calls.append(
            {
                "query": query, "bot_id": bot_id,
                "experience_type": experience_type,
            }
        )
        if experience_type is None:
            return []
        return list(self._fixtures.get(experience_type, []))


class _StubLLM(TaskExperienceLLMProtocol):
    async def chat(self, *, system: str, user: str,
                   max_tokens: int = 2000, temperature: float = 0.1) -> str:
        return TASK_EXPERIENCE_LLM_DISABLED


# --- method A ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_method_a_empty_when_no_workflow_experience() -> None:
    policy = _StubPolicyRetrieval(fixtures={
        "multibot_workflow": [],
    })
    bot_tags = _StubBotTags()

    plan = await method_a_workflow(
        question="如何部署 service", bot_id="bot-driver",
        policy=policy, bot_tags=bot_tags,
    )

    assert plan.method == "A"
    assert plan.tasks == ()
    assert plan.has_policy_support is False
    assert plan.reason == "method_a_no_experience"
    assert len(policy.calls) == 1
    assert policy.calls[0]["experience_type"] == "multibot_workflow"


@pytest.mark.asyncio
async def test_method_a_policy_disabled_returns_empty_with_reason() -> None:
    """A None policy (community-noop bound) returns the disabled-empty plan."""
    bot_tags = _StubBotTags()
    plan = await method_a_workflow(
        question="x", bot_id="b",
        policy=None,  # type: ignore[arg-type]
        bot_tags=bot_tags,
    )
    assert plan.method == "A"
    assert plan.reason == "kgbase_disabled"
    assert plan.tasks == ()
    assert plan.has_policy_support is False


@pytest.mark.asyncio
async def test_method_a_expands_chain_into_two_segments() -> None:
    wf_policy = KGBasePolicy(
        id="wf-1",
        properties={
            "bot_id": "b1",
            "experience_type": "multibot_workflow",
            "status": "active",
            "boundary": "识别并创建协作会话",
            "collaboration_chain": '["b1","b2","b3"]',
            "gain": "8.0",
            "trace_duration": "100.0",
        },
    )
    policy = _StubPolicyRetrieval(fixtures={
        "multibot_workflow": [wf_policy],
    })
    bot_tags = _StubBotTags({"b2": "alisd2", "b3": "alisd3"})

    plan = await method_a_workflow(
        question="how to do X", bot_id="b1",
        policy=policy, bot_tags=bot_tags,
    )

    assert plan.method == "A"
    assert plan.has_policy_support is True
    # driver (1) + chain[1:] (2) → 3 tasks
    assert len(plan.tasks) == 3
    driver = plan.tasks[0]
    assert driver.action == "识别并创建协作会话"
    assert driver.bot_id == "b1"
    assert driver.duration == 10.0
    assert driver.policy_type == "multibot_workflow"
    assert driver.policy_id == "wf-1"
    # downstream chain bots get the 90% split divided by len(chain)-1
    downstream = plan.tasks[1:]
    assert {t.bot_id for t in downstream} == {"b2", "b3"}
    for t in downstream:
        assert t.duration == 45.0
        assert t.policy_id == "wf-1"
        assert t.policy_type == "multibot_workflow"
    # bot_tags resolved b2/b3 names
    assert all(
        "alisd2" in t.action or "alisd3" in t.action
        for t in downstream
    )


# --- method C ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_method_c_retrieves_three_experience_types_per_step() -> None:
    cap = KGBasePolicy(
        id="cap-1",
        properties={
            "bot_id": "b1", "status": "active", "experience_type": "capability_boundary",
            "trace_duration": "10.0", "gain": "5.0",
            "target_bot_id": "b2",
        },
    )
    sp = KGBasePolicy(
        id="sp-1",
        properties={
            "bot_id": "b1", "status": "active", "experience_type": "success_pattern",
            "trace_duration": "30.0", "gain": "7.0",
        },
    )
    policy = _StubPolicyRetrieval(fixtures={
        "capability_boundary": [cap],
        "success_pattern": [sp],
        "multibot_workflow": [],
    })
    bot_tags = _StubBotTags({"b2": "alu"})

    plan = await method_c_incremental(
        question="请求处理 X", bot_id="b1",
        policy=policy, bot_tags=bot_tags,
        max_rounds=2,
    )

    assert plan.method == "C"
    assert plan.has_policy_support is True
    # First round emitted cap (cap_task) + sp_task + a new sub-task "由 alu 处理: Q"
    # Second round pulls the sub-task (target=b2) but b2 has no fixtures →
    # covers with policy_type=None fallback
    assert len(plan.tasks) >= 3
    cap_task = next(t for t in plan.tasks if t.policy_type == "capability_boundary")
    assert cap_task.policy_id == "cap-1"
    assert cap_task.duration == 3.0  # 10 * 0.3
    sp_task = next(t for t in plan.tasks if t.policy_type == "success_pattern")
    assert sp_task.policy_id == "sp-1"
    assert sp_task.duration == 30.0
    assert sp_task.gain == 7.0


@pytest.mark.asyncio
async def test_method_c_no_retrieval_returns_no_policy_plan() -> None:
    policy = _StubPolicyRetrieval(fixtures={})
    bot_tags = _StubBotTags()
    plan = await method_c_incremental(
        question="anything", bot_id="b1",
        policy=policy, bot_tags=bot_tags,
        max_rounds=2,
    )
    assert plan.method == "C"
    assert plan.has_policy_support is False
    assert plan.tasks  # at least the unplaced fallback
    assert all(t.policy_type is None for t in plan.tasks)


# --- TaskPlanService generate_task_plans + ranking -------------------------

@pytest.mark.asyncio
async def test_generate_task_plans_recommends_lowest_duration_when_policy_tied() -> None:
    """When two methods both surface the same multibot_workflow policy with
    equal gain, the ranking tie-breaker (total_duration ascending) wins —
    method C emits only the downstream chain tasks while method A also emits
    the driver step (~10% of trace_duration), so C's total_duration < A's
    and the recommendation is C."""
    wf_policy = KGBasePolicy(
        id="wf-1",
        properties={
            "bot_id": "b1", "status": "active", "experience_type": "multibot_workflow",
            "boundary": "driver step", "collaboration_chain": '["b1","b2"]',
            "gain": "9.0", "trace_duration": "20.0",
        },
    )
    policy = _StubPolicyRetrieval(fixtures={"multibot_workflow": [wf_policy]})
    bot_tags = _StubBotTags({"b2": "alisd2"})
    service = TaskPlanService(
        llm=_StubLLM(),
        policy=policy,
        bot_tags=bot_tags,
    )

    report: PlanReport = await service.generate_task_plans(
        question="how to ship", bot_id="b1",
    )

    # 4 plans in the original A/B/C/D order
    assert [p.method for p in report.plans] == ["A", "B", "C", "D"]
    # Two policy-backed plans tie on has_policy_support + gain; C has a shorter
    # total_duration (only downstream) so it ranks first.
    plan_a = report.plans[0]
    plan_c = report.plans[2]
    assert plan_a.has_policy_support is True
    assert plan_c.has_policy_support is True
    assert plan_a.total_duration > plan_c.total_duration
    assert report.recommendation == "C"
    # method B/D depend on the LLM, which is disabled in this test fixture
    # (stub LLM returns the disabled sentinel) → both return llm_disabled
    # empty plans.
    plan_b = report.plans[1]
    assert plan_b.has_policy_support is False
    assert plan_b.reason == "llm_disabled"
    plan_d = report.plans[3]
    assert plan_d.has_policy_support is False
    assert plan_d.reason == "llm_disabled"


@pytest.mark.asyncio
async def test_generate_task_plans_no_retrieval_falls_back_and_keeps_recommendation_a() -> None:
    """When policy is a community-noop (returns []), method A becomes empty
    with ``method_a_no_experience`` and method C produces a single fallback
    task with ``policy_type=None`` (placeholder step). All plans have
    has_policy_support=False and zero gain; total_duration is 0 everywhere,
    so the stable sort keeps ranked[0]=A and recommendation stays "A"."""
    policy = _StubPolicyRetrieval(fixtures={})
    bot_tags = _StubBotTags()
    service = TaskPlanService(llm=_StubLLM(), policy=policy, bot_tags=bot_tags)

    report = await service.generate_task_plans(question="q", bot_id="b")
    assert all(p.has_policy_support is False for p in report.plans)
    plan_a = report.plans[0]
    assert plan_a.tasks == ()
    assert plan_a.reason == "method_a_no_experience"
    plan_c = report.plans[2]
    # method C falls back to a single non-policy placeholder
    assert plan_c.tasks  # at least one
    assert all(t.policy_type is None for t in plan_c.tasks)
    # A5 method B/D both depend on the LLM; stub returns the disabled sentinel,
    # so they emit empty plans with reason="llm_disabled".
    plan_b = report.plans[1]
    assert plan_b.tasks == ()
    assert plan_b.reason == "llm_disabled"
    plan_d = report.plans[3]
    assert plan_d.tasks == ()
    assert plan_d.reason == "llm_disabled"
    # All zero gain / zero duration → stable sort, ranked[0] is A
    assert report.recommendation == "A"


@pytest.mark.asyncio
async def test_generate_task_plans_protocol_typing_round_trips() -> None:
    """Construct-time Protocol conformance check (runtime_checkable)."""
    bot_tags = _StubBotTags()
    policy = _StubPolicyRetrieval()
    llm = _StubLLM()
    assert isinstance(llm, TaskExperienceLLMProtocol)
    assert isinstance(policy, PolicyRetrievalPort)
    assert isinstance(bot_tags, BotTagsPort)
    service = TaskPlanService(llm=llm, policy=policy, bot_tags=bot_tags)
    assert isinstance(service, TaskPlanService)
