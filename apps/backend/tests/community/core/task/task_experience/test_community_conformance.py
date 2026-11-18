"""Community conformance runner for task_experience Protocols.

Wires the community-default builders (``OpenAICompatibleTaskExperienceLLM``,
``_NoopPolicyRetrieval``, ``_IdentityBotTags``, ``TaskPlanService``,
``AcceptanceExpanderService``) through the conformance suites shipped under
``conformance/``. Failing any of these is a Protocol contract break and must
be raised here, not silently absorbed by upstream consumers.

The corp composition root in OCB (Task B4 / ``CorpTaskExperienceIntegration``)
re-imports the same ``conformance.assert_xxx_invariants`` functions and runs
them against its corp providers: any corp-side regression surfaces as an OCB
test failure, mirroring the community contract.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.task.task_experience.adapters.llm import (
    OpenAICompatibleTaskExperienceLLM,
)
from agentclaw.community.core.task.task_experience.services.acceptance_expander_service import (
    AcceptanceExpanderService,
)
from agentclaw.community.core.task.task_experience.services.task_plan_service import (
    TaskPlanService,
)

from .conformance._stubs import (
    _DisabledLLM,
    _FakeHttpClient,
    _FakeSecretResolver,
    _IdentityBotTags,
    _NoopPolicyRetrieval,
)
from .conformance.acceptance_expander_service_protocol_conformance import (
    assert_acceptance_expander_service_protocol_invariants,
)
from .conformance.bot_tags_port_conformance import (
    assert_bot_tags_port_invariants,
)
from .conformance.llm_protocol_conformance import (
    assert_task_experience_llm_invariants,
)
from .conformance.policy_retrieval_port_conformance import (
    assert_policy_retrieval_port_invariants,
)
from .conformance.task_plan_service_protocol_conformance import (
    assert_task_plan_service_protocol_invariants,
)


# --- LLM builder -----------------------------------------------------------


def _make_llm(
    *,
    base_url: str = "https://antchat.example/v1",
    secret_name: str = "task_experience_antllm_key",
    secret_token: str | None = "tok",
    response_content: str = "hello world   ",
    timeout_ms: int = 1_000,
):
    payload = {"choices": [{"message": {"content": response_content}}]}
    return OpenAICompatibleTaskExperienceLLM(
        base_url=base_url,
        secret_name=secret_name,
        model="DeepSeek-V4-Flash",
        timeout_ms=timeout_ms,
        secret_resolver=_FakeSecretResolver(secret_token),
        http_client=_FakeHttpClient(payload),
    )


# --- Port / service builders -----------------------------------------------


def _make_policy_port():
    return _NoopPolicyRetrieval()


def _make_bot_tags():
    return _IdentityBotTags()


def _make_plan_service():
    return TaskPlanService(
        llm=_DisabledLLM(),
        policy=_make_policy_port(),
        bot_tags=_make_bot_tags(),
    )


def _make_acceptance_service():
    return AcceptanceExpanderService(
        policy=_make_policy_port(),
        bot_tags=_make_bot_tags(),
    )


# --- Suite tests ------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_protocol_invariants_community() -> None:
    await assert_task_experience_llm_invariants(_make_llm)


@pytest.mark.asyncio
async def test_policy_retrieval_port_invariants_community() -> None:
    await assert_policy_retrieval_port_invariants(_make_policy_port)


def test_bot_tags_port_invariants_community() -> None:
    assert_bot_tags_port_invariants(_make_bot_tags)


@pytest.mark.asyncio
async def test_task_plan_service_protocol_invariants_community() -> None:
    await assert_task_plan_service_protocol_invariants(_make_plan_service)


@pytest.mark.asyncio
async def test_acceptance_expander_service_protocol_invariants_community() -> None:
    await assert_acceptance_expander_service_protocol_invariants(
        _make_acceptance_service,
    )
