"""Community-default DI wiring for the task_experience module.

Wires the four ports and both services:

- ``TaskExperienceLLMProtocol`` → ``OpenAICompatibleTaskExperienceLLM``
  configured from :class:`TaskExperienceLLMConfig` (the ``task_experience.llm``
  user_config block, parsed by :class:`TaskExperienceConfigModule`).
  Community default leaves the block unset, so the ``OpenAICompatibleTask…LLM``
  sentinel kicks in (``base_url=""`` ⇒ ``_resolve_token`` short-circuits,
  ``chat()`` returns ``TASK_EXPERIENCE_LLM_DISABLED`` without HTTP) and
  community tests never touch the network.
- ``PolicyRetrievalPort`` → ``_NoopPolicyRetrieval`` returning ``[]``; KGBase
  is a corp-owned upstream and Community mode must not require it (arch rule
  20 / single-box dev first).
- ``BotTagsPort`` → ``_IdentityBotTags`` (``bot_name(i) == i``).
- ``TaskPlanServiceProtocol`` → ``TaskPlanService`` (LLM disabled + KGBase
  no-op ⇒ ``generate_task_plans`` emits four empty plans and recommends A).
- ``AcceptanceExpanderServiceProtocol`` → ``AcceptanceExpanderService`` (KGBase
  no-op ⇒ ``expand_acceptance_criteria`` returns ``note='kgbase_disabled'``).

Real configuration / credential / HTTP bindings for PROD / prepub / corp_test
profiles come from the corp composition root ``CorpTaskExperienceIntegration``
in the OCB repository; the corp module reaches the same Protocol bindings
through the standard injector override mechanism.
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
from agentclaw.community.core.task.task_experience.types import KGBasePolicy
from agentclaw.community.di.task_experience_config import (
    TaskExperienceLLMConfig,
)
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_GENERAL,
    HttpClient,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

__all__ = ["TaskExperienceModule"]


class _NoopPolicyRetrieval(PolicyRetrievalPort):
    """Community default: ``retrieve`` always returns ``[]``.

    KGBase is a corp-owned upstream; community / local / test mode never
    reaches it. Service-level callers treat empty results as the same path
    as "disabled upstream", so no caller needs to special-case this no-op.
    """

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
    """Community default: ``bot_name`` is identity (``bot_id`` unchanged)."""

    def bot_name(self, bot_id: str) -> str:
        return bot_id


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
        # base_url/secret_name are read from the TaskExperienceLLMConfig block.
        # When the block is unset (community default), the OpenAI-compatible
        # adapter short-circuits at _resolve_token / chat time and returns
        # the disabled sentinel without any HTTP. A configured block wires
        # the same adapter with prod values via Bcom-compatible DI overrides
        # upstream from the corp composition root.
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
    @inject
    def task_plan_service(
        self,
        llm: TaskExperienceLLMProtocol,
        policy: PolicyRetrievalPort,
        bot_tags: BotTagsPort,
    ) -> TaskPlanServiceProtocol:
        return TaskPlanService(llm=llm, policy=policy, bot_tags=bot_tags)

    @singleton
    @provider
    @inject
    def acceptance_expander_service(
        self,
        policy: PolicyRetrievalPort,
        bot_tags: BotTagsPort,
    ) -> AcceptanceExpanderServiceProtocol:
        return AcceptanceExpanderService(policy=policy, bot_tags=bot_tags)
