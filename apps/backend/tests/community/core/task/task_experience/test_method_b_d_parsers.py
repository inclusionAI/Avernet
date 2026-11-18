"""Method B/D LLM-parse path tests (the four model-return shapes).

Covers the A5 acceptance points:
  - pure JSON array
  - fenced JSON (```` ```json ... ``` ```` or ```...``` with no language tag)
  - JSON + trailing text after the JSON
  - sentinel 如何被识别，返回 ``llm_disabled``
  - invalid JSON / non-list body → ``invalid_model_output``
  - LLM raises ``ValueError`` (forum-style mess) → ``invalid_model_output``
Method B also exercises the KGBase success_pattern backfill when the LLM
returns parseable steps and the policy port returns a ``success_pattern`` row.
Stubs implement the runtime_checkable Protocols; no network.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    PolicyRetrievalPort,
    TaskExperienceLLMProtocol,
)
from agentclaw.community.core.task.task_experience.services._methods.method_b_llm_decompose import (
    method_b_llm_decompose,
)
from agentclaw.community.core.task.task_experience.services._methods.method_d_llm_self_plan import (
    method_d_llm_self_plan,
)
from agentclaw.community.core.task.task_experience.types import (
    KGBasePolicy,
    TASK_EXPERIENCE_LLM_DISABLED,
)


class _ControllableStubLLM(TaskExperienceLLMProtocol):
    """Returns a fixed canned response or raises a chosen exception."""

    def __init__(self, *, response: str | None = None, raises: Exception | None = None) -> None:
        self._response = response if response is not None else ""
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    async def chat(self, *, system: str, user: str,
                   max_tokens: int = 2000, temperature: float = 0.1) -> str:
        self.calls.append({
            "system": system, "user": user,
            "max_tokens": max_tokens, "temperature": temperature,
        })
        if self._raises is not None:
            raise self._raises
        return self._response


class _IdentityBotTags(BotTagsPort):
    def bot_name(self, bot_id: str) -> str:
        return bot_id


class _StubPolicy(PolicyRetrievalPort):
    def __init__(self, fixtures: dict[str, list[KGBasePolicy]] | None = None) -> None:
        self._fixtures = fixtures or {}

    async def retrieve(
        self, query: str, *, bot_id: str,
        entity_type: str = "Policy", property_name: str = "trigger",
        top_k: int = 5, min_score: float = 0.3,
        experience_type: str | None = None, active_only: bool = True,
    ) -> list[KGBasePolicy]:
        if experience_type is None:
            return []
        return list(self._fixtures.get(experience_type, []))


# --- method B --------------------------------------------------------------

@pytest.mark.asyncio
async def test_method_b_disabled_sentinel_returns_empty_plan() -> None:
    llm = _ControllableStubLLM(response=TASK_EXPERIENCE_LLM_DISABLED)
    plan = await method_b_llm_decompose(
        question="q", bot_id="b1",
        llm=llm, policy=_StubPolicy(), bot_tags=_IdentityBotTags(),
    )
    assert plan.method == "B"
    assert plan.tasks == ()
    assert plan.has_policy_support is False
    assert plan.reason == "llm_disabled"
    # Sentinel detection happens before any further parsing
    assert len(llm.calls) == 1
    assert llm.calls[0]["max_tokens"] == 500


@pytest.mark.asyncio
async def test_method_b_llm_raises_returns_invalid_model_output() -> None:
    llm = _ControllableStubLLM(raises=ValueError("model blew up"))
    plan = await method_b_llm_decompose(
        question="q", bot_id="b1",
        llm=llm, policy=_StubPolicy(), bot_tags=_IdentityBotTags(),
    )
    assert plan.reason == "invalid_model_output"
    assert plan.tasks == ()


@pytest.mark.asyncio
async def test_method_b_pure_json_array_emits_no_policy_tasks_without_retrieval() -> None:
    llm = _ControllableStubLLM(response='["查配置", "查日志", "改设置"]')
    plan = await method_b_llm_decompose(
        question="q", bot_id="b1",
        llm=llm, policy=_StubPolicy(), bot_tags=_IdentityBotTags(),
    )
    assert plan.method == "B"
    assert plan.has_policy_support is False
    # One task per step description, all without policy backfill
    assert len(plan.tasks) == 3
    assert [t.action for t in plan.tasks] == ["查配置", "查日志", "改设置"]
    for t in plan.tasks:
        assert t.policy_type is None
        assert t.policy_id is None
        assert t.gain == 0.0
        assert t.duration == 0.0


@pytest.mark.asyncio
async def test_method_b_fenced_json_array_strips_backticks() -> None:
    fenced = "```json\n[\"step one\", \"step two\"]\n```"
    llm = _ControllableStubLLM(response=fenced)
    plan = await method_b_llm_decompose(
        question="q", bot_id="b1",
        llm=llm, policy=_StubPolicy(), bot_tags=_IdentityBotTags(),
    )
    assert plan.method == "B"
    assert len(plan.tasks) == 2
    assert [t.action for t in plan.tasks] == ["step one", "step two"]


@pytest.mark.asyncio
async def test_method_b_non_list_json_returns_invalid_model_output() -> None:
    # JSON object, not a JSON array list body ⇒ reject the row
    llm = _ControllableStubLLM(response='{"steps": ["a"]}')
    plan = await method_b_llm_decompose(
        question="q", bot_id="b1",
        llm=llm, policy=_StubPolicy(), bot_tags=_IdentityBotTags(),
    )
    assert plan.reason == "invalid_model_output"


@pytest.mark.asyncio
async def test_method_b_success_pattern_backfill_attaches_policy() -> None:
    sp = KGBasePolicy(
        id="sp-1",
        properties={"bot_id": "b1", "status": "active",
                    "experience_type": "success_pattern",
                    "trace_duration": "12.0", "gain": "4.5"},
    )
    # Stub policy returns the same fixture for every success_pattern retrieve
    # (it ignores query), so every step picks up the same success_pattern row;
    # the test focus is on whether backfill attaches policy fields rather than
    # the schema of retrieve-by-query.
    fixtures = {"success_pattern": [sp]}
    llm = _ControllableStubLLM(response='["查odc数据", "执行推送"]')
    plan = await method_b_llm_decompose(
        question="q", bot_id="b1",
        llm=llm, policy=_StubPolicy(fixtures=fixtures),
        bot_tags=_IdentityBotTags(),
    )
    assert plan.has_policy_support is True
    assert len(plan.tasks) == 2
    step1 = plan.tasks[0]
    assert step1.action == "查odc数据"
    assert step1.policy_type == "success_pattern"
    assert step1.policy_id == "sp-1"
    assert step1.duration == 12.0
    assert step1.gain == 4.5
    step2 = plan.tasks[1]
    # Both steps share the same fixture ⇒ both are backfilled.
    assert step2.policy_type == "success_pattern"
    assert step2.policy_id == "sp-1"
    assert step2.bot_id == "b1"  # success_pattern keeps bot_id unchanged


@pytest.mark.asyncio
async def test_method_b_no_backfill_when_policy_port_empty_returns_no_policy_plan() -> None:
    """Policy fixture lists are empty ⇒ not one policy attachment ⇒
    plan.has_policy_support is False even if LLM produced steps."""
    llm = _ControllableStubLLM(response='["step a", "step b"]')
    plan = await method_b_llm_decompose(
        question="q", bot_id="b1",
        llm=llm, policy=_StubPolicy(fixtures={}),
        bot_tags=_IdentityBotTags(),
    )
    assert plan.method == "B"
    assert plan.has_policy_support is False
    assert len(plan.tasks) == 2
    for t in plan.tasks:
        assert t.policy_type is None
        assert t.policy_id is None


@pytest.mark.asyncio
async def test_method_b_capability_boundary_backfill_attaches_target_bot() -> None:
    cap = KGBasePolicy(
        id="cap-1",
        properties={"bot_id": "b1", "status": "active",
                    "experience_type": "capability_boundary",
                    "trace_duration": "8.0", "gain": "2.0",
                    "target_bot_id": "b2"},
    )
    fixtures = {"success_pattern": [], "capability_boundary": [cap]}
    llm = _ControllableStubLLM(response='["诊断 cron"]')
    plan = await method_b_llm_decompose(
        question="q", bot_id="b1",
        llm=llm, policy=_StubPolicy(fixtures=fixtures),
        bot_tags=_IdentityBotTags(),
    )
    assert plan.has_policy_support is True
    task = plan.tasks[0]
    assert task.action == "诊断 cron"
    assert task.bot_id == "b2"  # cap target_bot_id replaces caller bot
    assert task.policy_type == "capability_boundary"
    assert task.policy_id == "cap-1"


# --- method D --------------------------------------------------------------

@pytest.mark.asyncio
async def test_method_d_disabled_sentinel_returns_empty_plan() -> None:
    llm = _ControllableStubLLM(response=TASK_EXPERIENCE_LLM_DISABLED)
    plan = await method_d_llm_self_plan(
        question="q", bot_id="b1",
        llm=llm, bot_tags=_IdentityBotTags(),
    )
    assert plan.method == "D"
    assert plan.tasks == ()
    assert plan.has_policy_support is False
    assert plan.reason == "llm_disabled"


@pytest.mark.asyncio
async def test_method_d_pure_json_object_array_builds_tasks_kept_bot_id() -> None:
    body = '[{"action":"步骤1","bot_id":"b1"}, {"action":"步骤2","bot_id":"b1"}]'
    llm = _ControllableStubLLM(response=body)
    plan = await method_d_llm_self_plan(
        question="q", bot_id="b1",
        llm=llm, bot_tags=_IdentityBotTags(),
    )
    assert plan.method == "D"
    assert plan.has_policy_support is False
    assert len(plan.tasks) == 2
    assert all(t.bot_id == "b1" for t in plan.tasks)
    assert [t.action for t in plan.tasks] == ["步骤1", "步骤2"]


@pytest.mark.asyncio
async def test_method_d_fenced_json_object_array_works() -> None:
    body = "```\n[{\"action\":\"x\",\"bot_id\":\"b1\"}]\n```"
    llm = _ControllableStubLLM(response=body)
    plan = await method_d_llm_self_plan(
        question="q", bot_id="b1",
        llm=llm, bot_tags=_IdentityBotTags(),
    )
    assert len(plan.tasks) == 1
    assert plan.tasks[0].action == "x"


@pytest.mark.asyncio
async def test_method_d_non_dict_items_rejected_as_invalid() -> None:
    body = '["only strings here", 12, true]'
    llm = _ControllableStubLLM(response=body)
    plan = await method_d_llm_self_plan(
        question="q", bot_id="b1",
        llm=llm, bot_tags=_IdentityBotTags(),
    )
    # All entries are non-dict → result task_dicts is empty → invalid_model_output
    assert plan.tasks == ()
    assert plan.reason == "invalid_model_output"


@pytest.mark.asyncio
async def test_method_d_non_list_json_returns_invalid_model_output() -> None:
    llm = _ControllableStubLLM(response='{"action": "plain"}')
    plan = await method_d_llm_self_plan(
        question="q", bot_id="b1",
        llm=llm, bot_tags=_IdentityBotTags(),
    )
    assert plan.reason == "invalid_model_output"


@pytest.mark.asyncio
async def test_method_d_missing_bot_id_falls_back_to_caller() -> None:
    body = '[{"action":"a"}, {"action":"b","bot_id":"b2"}]'
    llm = _ControllableStubLLM(response=body)
    plan = await method_d_llm_self_plan(
        question="q", bot_id="b1",
        llm=llm, bot_tags=_IdentityBotTags(),
    )
    assert len(plan.tasks) == 2
    assert plan.tasks[0].bot_id == "b1"
    assert plan.tasks[1].bot_id == "b2"


@pytest.mark.asyncio
async def test_method_d_llm_raises_returns_invalid_model_output() -> None:
    llm = _ControllableStubLLM(raises=ValueError("oops"))
    plan = await method_d_llm_self_plan(
        question="q", bot_id="b1",
        llm=llm, bot_tags=_IdentityBotTags(),
    )
    assert plan.reason == "invalid_model_output"
    assert plan.tasks == ()
