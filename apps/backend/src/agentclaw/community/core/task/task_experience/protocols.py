"""Transport-agnostic ports for the task_experience module.

Mirrors the forum/shared/protocols.py shape: every capability the
task_experience services consume (LLM chat, KGBase policy retrieval, bot tag
lookup) is a ``@runtime_checkable`` Protocol so the corporate composition root
can wire real implementations while community tests inject lightweight fakes
without touching networking or secrets.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.task_experience.types import (
    AcceptanceReport,
    KGBasePolicy,
    Plan,
    PlanReport,
)


@runtime_checkable
class TaskExperienceLLMProtocol(Protocol):
    """OpenAI-compatible chat-completion seam used by methods B and D.

    Implementations return the well-known ``TASK_EXPERIENCE_LLM_DISABLED``
    sentinel string when no ``base_url`` or credential is configured; business
    code treats the sentinel as "upstream disabled" and records a reason
    instead of raising.
    """

    @abstractmethod
    async def chat(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
    ) -> str:
        """Return one model-completion string, or the disabled sentinel."""


@runtime_checkable
class PolicyRetrievalPort(Protocol):
    """Polymorphic policy retrieval seam read by methods A, B, C and the
    acceptance expander.

    Returns an empty list (rather than raising) when KGBase is unreachable or
    configured as disabled in the active profile; corporate implementations
    log ``reason`` so diagnosis survives the silent-skip path. ``KGBasePolicy``
    is the only projection crossing this boundary; community core never
    imports a KGBase client type.
    """

    @abstractmethod
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
        """Return policies matching the query; empty list when upstream disabled."""


@runtime_checkable
class BotTagsPort(Protocol):
    """Maps a ``bot_id`` to its display name for prompt-building.

    The community default is identity (``bot_name(i) == i``). Corporate
    implementations consult corp bot registry / config ``bot_tags.map`` before
    falling back to identity.
    """

    @abstractmethod
    def bot_name(self, bot_id: str) -> str:
        """Return bot display name; fallback to ``bot_id`` when unknown."""


@runtime_checkable
class TaskPlanServiceProtocol(Protocol):
    """Aggregate service producing the four candidate plans plus a recommendation."""

    @abstractmethod
    async def generate_task_plans(
        self,
        question: str,
        bot_id: str,
    ) -> PlanReport:
        """Produce a :class:`PlanReport` for ``question`` and ``bot_id``."""


@runtime_checkable
class AcceptanceExpanderServiceProtocol(Protocol):
    """Service that expands the acceptance criteria of a question using
    ``capability_boundary`` experience policies."""

    @abstractmethod
    async def expand_acceptance_criteria(
        self,
        question: str,
        bot_id: str,
    ) -> AcceptanceReport:
        """Produce an :class:`AcceptanceReport` for ``question`` and ``bot_id``."""


__all__ = [
    "TaskExperienceLLMProtocol",
    "PolicyRetrievalPort",
    "BotTagsPort",
    "TaskPlanServiceProtocol",
    "AcceptanceExpanderServiceProtocol",
    "MethodAWorkflowPort",
    "MethodBDecomposePort",
    "MethodCIncrementalPort",
    "MethodDSelfPlanPort",
]


@runtime_checkable
class MethodAWorkflowPort(Protocol):
    """Method A: multibot_workflow direct reuse from KGBase experience.

    Corp-owned algorithm; community provides a disabled placeholder that
    returns an empty plan with ``reason='method_implemented_in_corp'``.
    """

    @abstractmethod
    async def generate(
        self,
        *,
        question: str,
        bot_id: str,
        policy: "PolicyRetrievalPort",
        bot_tags: "BotTagsPort",
    ) -> Plan:
        """Generate a plan from multibot_workflow experience policies."""


@runtime_checkable
class MethodBDecomposePort(Protocol):
    """Method B: LLM decompose + KGBase success_pattern backfill.

    Corp-owned algorithm; community provides a disabled placeholder.
    """

    @abstractmethod
    async def generate(
        self,
        *,
        question: str,
        bot_id: str,
        llm: "TaskExperienceLLMProtocol",
        policy: "PolicyRetrievalPort",
        bot_tags: "BotTagsPort",
    ) -> Plan:
        """Generate a plan by LLM-decomposing steps and backfilling policies."""


@runtime_checkable
class MethodCIncrementalPort(Protocol):
    """Method C: incremental rewrite + full-policy retrieval from KGBase.

    Corp-owned algorithm; community provides a disabled placeholder.
    """

    @abstractmethod
    async def generate(
        self,
        *,
        question: str,
        bot_id: str,
        policy: "PolicyRetrievalPort",
        bot_tags: "BotTagsPort",
    ) -> Plan:
        """Generate a plan by incrementally rewriting + retrieving policies."""


@runtime_checkable
class MethodDSelfPlanPort(Protocol):
    """Method D: LLM self-plan (no policy lookup).

    Corp-owned algorithm; community provides a disabled placeholder.
    """

    @abstractmethod
    async def generate(
        self,
        *,
        question: str,
        bot_id: str,
        llm: "TaskExperienceLLMProtocol",
        bot_tags: "BotTagsPort",
    ) -> Plan:
        """Generate a plan by asking the LLM to self-plan."""
