"""Community-default DI wiring for the task_experience module.

Wires the shared ports, the four method Ports (disabled placeholders), and
both services:

- ``TaskExperienceLLMProtocol`` → ``OpenAICompatibleTaskExperienceLLM``
  configured from :class:`TaskExperienceLLMConfig`.
- ``PolicyRetrievalPort`` → ``_NoopPolicyRetrieval`` returning ``[]``.
- ``BotTagsPort`` → ``_IdentityBotTags`` (``bot_name(i) == i``).
- ``MethodAWorkflowPort`` / ``MethodBDecomposePort`` /
  ``MethodCIncrementalPort`` / ``MethodDSelfPlanPort`` → disabled
  placeholders returning empty plans with ``reason='method_implemented_in_corp'``.
  Real algorithm implementations live in the corp composition root.
- ``TaskPlanServiceProtocol`` → ``TaskPlanService`` (injects the 4 method
  Ports + shared ports; all-disabled ⇒ 4 empty plans, recommends A).
- ``AcceptanceExpanderServiceProtocol`` → ``AcceptanceExpanderService``.

The corp composition root ``CorpTaskExperienceIntegration`` in OCB replaces
the method Port bindings with real algorithm implementations and the
shared-Port bindings with corp-owned KGBase / LLM / bot-tag providers.
"""

from __future__ import annotations

from typing import Annotated

from injector import Module, inject, provider, singleton

from agentclaw.community.core.task.task_experience.adapters.llm import (
    OpenAICompatibleTaskExperienceLLM,
)
from agentclaw.community.core.task.task_experience.protocols import (
    AcceptanceExpanderServiceProtocol,
    BotTagsPort,
    MethodAWorkflowPort,
    MethodBDecomposePort,
    MethodCIncrementalPort,
    MethodDSelfPlanPort,
    PolicyRetrievalPort,
    TaskExperienceLLMProtocol,
    TaskPlanServiceProtocol,
)
from agentclaw.community.core.task.task_experience.services.acceptance_expander_service import (
    AcceptanceExpanderService,
)
from agentclaw.community.core.task.task_experience.services.task_plan_service import (
    TaskPlanService,
)
from agentclaw.community.core.task.task_experience.types import (
    KGBasePolicy,
    Plan,
)
from agentclaw.community.di.task_experience_config import (
    TaskExperienceLLMConfig,
)
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_GENERAL,
    HttpClient,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

__all__ = ["TaskExperienceModule"]


def _disabled_plan(method: str) -> Plan:
    """Empty plan with ``reason='method_implemented_in_corp'`` placeholder."""
    return Plan(
        method=method,
        tasks=tuple(),
        total_duration=0.0,
        gain=0.0,
        has_policy_support=False,
        links={},
        reason="method_implemented_in_corp",
    )


class _NoopPolicyRetrieval(PolicyRetrievalPort):
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
        return []


class _IdentityBotTags(BotTagsPort):
    def bot_name(self, bot_id: str) -> str:
        return bot_id


class _DisabledMethodA(MethodAWorkflowPort):
    async def generate(self, *, question, bot_id, policy, bot_tags) -> Plan:
        return _disabled_plan("A")


class _DisabledMethodB(MethodBDecomposePort):
    async def generate(self, *, question, bot_id, llm, policy, bot_tags) -> Plan:
        return _disabled_plan("B")


class _DisabledMethodC(MethodCIncrementalPort):
    async def generate(self, *, question, bot_id, policy, bot_tags) -> Plan:
        return _disabled_plan("C")


class _DisabledMethodD(MethodDSelfPlanPort):
    async def generate(self, *, question, bot_id, llm, bot_tags) -> Plan:
        return _disabled_plan("D")


class TaskExperienceModule(Module):
    """Composition root for the task_experience services (community default)."""

    @singleton
    @provider
    @inject
    def task_experience_llm(
        self,
        config: TaskExperienceLLMConfig,
        secret_resolver: SecretResolver,
        general_http: Annotated[HttpClient, QUALIFIER_GENERAL],
    ) -> TaskExperienceLLMProtocol:
        return OpenAICompatibleTaskExperienceLLM(
            base_url=config.base_url,
            secret_name=config.secret_name,
            model=config.model,
            timeout_ms=config.timeout_ms,
            secret_resolver=secret_resolver,
            http_client=general_http,
        )

    @singleton
    @provider
    def task_experience_policy_retrieval(self) -> PolicyRetrievalPort:
        return _NoopPolicyRetrieval()

    @singleton
    @provider
    def task_experience_bot_tags(self) -> BotTagsPort:
        return _IdentityBotTags()

    @singleton
    @provider
    def method_a_workflow(self) -> MethodAWorkflowPort:
        return _DisabledMethodA()

    @singleton
    @provider
    def method_b_decompose(self) -> MethodBDecomposePort:
        return _DisabledMethodB()

    @singleton
    @provider
    def method_c_incremental(self) -> MethodCIncrementalPort:
        return _DisabledMethodC()

    @singleton
    @provider
    def method_d_self_plan(self) -> MethodDSelfPlanPort:
        return _DisabledMethodD()

    @singleton
    @provider
    @inject
    def task_plan_service(
        self,
        llm: TaskExperienceLLMProtocol,
        policy: PolicyRetrievalPort,
        bot_tags: BotTagsPort,
        method_a: MethodAWorkflowPort,
        method_b: MethodBDecomposePort,
        method_c: MethodCIncrementalPort,
        method_d: MethodDSelfPlanPort,
    ) -> TaskPlanServiceProtocol:
        return TaskPlanService(
            llm=llm, policy=policy, bot_tags=bot_tags,
            method_a=method_a, method_b=method_b,
            method_c=method_c, method_d=method_d,
        )

    @singleton
    @provider
    @inject
    def acceptance_expander_service(
        self,
        policy: PolicyRetrievalPort,
        bot_tags: BotTagsPort,
    ) -> AcceptanceExpanderServiceProtocol:
        return AcceptanceExpanderService(policy=policy, bot_tags=bot_tags)
