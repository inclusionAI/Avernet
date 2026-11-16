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
    """Author an exact, immutable definition; all non-publication fields are required.

    ``publication=None`` explicitly declares no automatic Open publication use case.
    Creation does not allocate a version, install a profile or create an instance.
    """

    ref: VersionedProfileRef
    composition: MethodCompositionTemplate
    scenario_roles: tuple[ScenarioRole, ...]
    domain: DomainProfile
    workflow: WorkflowProfile
    governance: GovernanceRequirements
    compatibility: CompatibilityRequirements
    publication: OpenPublicationProfile | None = None
