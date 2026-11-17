"""Typed L6 contracts: profile authoring and explicit runtime instantiation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    CompatibilityRequirements,
    DomainProfile,
    GovernanceRequirements,
    MethodCompositionTemplate,
    ScenarioRole,
    VersionedProfileRef,
    WorkflowProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.instance import (
    ProductInstance,
)


@dataclass(frozen=True)
class ProductProfileCreateCommand:
    """Author an exact immutable definition; domain/orchestration are optional.

    Without workflow, a fixed versioned standard Task contract still applies.
    ``domain=None`` uses standard L3/L4 terms. Method-specific product policies
    belong to ``composition.policies``; an empty tuple declares none.
    Creation does not allocate a version, install a profile or create an instance.
    """

    ref: VersionedProfileRef
    composition: MethodCompositionTemplate
    scenario_roles: tuple[ScenarioRole, ...]
    governance: GovernanceRequirements
    compatibility: CompatibilityRequirements
    domain: DomainProfile | None = None
    workflow: WorkflowProfile | None = None
    task_contract_ref: VersionedProfileRef | None = None


@dataclass(frozen=True)
class ProductInstantiateCommand:
    """Pinned L6 selection on an untouched L4 Collet; not a creation input.

    A product's own role cardinality may be narrower than L4's executor set.
    """

    collet_id: str
    profile_ref: VersionedProfileRef
    expected_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.collet_id, str) or not self.collet_id.strip():
            raise ContextCollabValidationError("collet_id must be non-empty")
        if not isinstance(self.profile_ref, VersionedProfileRef):
            raise ContextCollabValidationError(
                "profile_ref must be a VersionedProfileRef"
            )
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise ContextCollabValidationError("expected_version must be positive")


class ProductInstantiationServiceProtocol(Protocol):
    def instantiate(
        self, command: ProductInstantiateCommand, context: ActorContext
    ) -> ProductInstance:
        """Authorize L6 selection on untouched L4 work, then initialize L5.

        Only the initiator/responsible principal may select. Product selection
        is immutable; stale, repeated or advanced-Collet selection is rejected.
        Selection does not rewrite initial L4 evidence or advance Task/Collet
        revisions. SQL selection and MethodStore remain separate transactions;
        binding errors propagate and existing recovery repairs missing bindings.
        """
        ...
