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
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    normalize_executor_refs,
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
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
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
    participant_scope: ParticipantScope = ParticipantScope()


@dataclass(frozen=True)
class ProductInstantiateCommand:
    """Pinned L6 selection on an untouched L4 Collet; not a creation input.

    A product's own role cardinality may be narrower than L4's executor set.
    Input resources are instance-scoped references, not uploads or Grants.
    """

    collet_id: str
    profile_ref: VersionedProfileRef
    expected_version: int
    participant_refs: tuple[AgentRef, ...] = ()
    # Omission uses the sole participant as the scalar role source; multiple
    # participants require explicit designation when an executor role is required.
    executor_ref: AgentRef | None = None
    resource_refs: tuple[ResourceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "participant_refs", normalize_executor_refs(self.participant_refs)
        )
        if not isinstance(self.resource_refs, (tuple, list)) or any(
            not isinstance(ref, ResourceRef) for ref in self.resource_refs
        ):
            raise ContextCollabValidationError(
                "resource_refs must contain typed ResourceRefs"
            )
        resources = tuple(self.resource_refs)
        for ref in resources:
            ResourceRef.from_dict(ref.to_dict())
        object.__setattr__(self, "resource_refs", resources)
        if (
            self.executor_ref is not None
            and self.executor_ref not in self.participant_refs
        ):
            raise ContextCollabValidationError(
                "executor_ref must be an initial participant"
            )
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
        """Authorize L6 selection on an untouched L4 Collet, then initialize L5.

        Only the initiator/responsible principal may select. Product selection
        is immutable; stale, repeated or advanced-Collet selection is rejected.
        Initial participants/roles and input resource references are bound at
        Collet scope in the same SQL transaction as the selection, without
        creating a Task or granting resource access. A binding Event advances
        Collet; root_task_ref stays None until Work.start. For pre-existing
        untouched Work, preserve its initial evidence
        and advance Task/Collet when assigning. SQL selection and MethodStore
        remain separate transactions;
        binding errors propagate and existing recovery repairs missing bindings.
        """
        ...
