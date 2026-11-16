"""Typed input contract for L6 product profile creation, not runtime installation."""

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    CompatibilityRequirements,
    DomainProfile,
    GovernanceRequirements,
    MethodCompositionTemplate,
    ScenarioRole,
    VersionedProfileRef,
    WorkflowProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenPublicationProfile,
)


@dataclass(frozen=True)
class ProductProfileCreateCommand:
    """Author an exact immutable definition; domain/orchestration are optional.

    Without workflow, a fixed versioned standard Task contract still applies.
    ``domain=None`` uses standard L3/L4 terms. ``publication=None`` declares
    no automatic Open publication use case.
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
    publication: OpenPublicationProfile | None = None
