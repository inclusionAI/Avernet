"""L6 ProductProfile creation: construct and validate, without installation or I/O."""

from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductProfileCreateCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.validation import (
    validate_product_profile,
)


def create_product_profile(
    command: ProductProfileCreateCommand,
) -> ProductProfileDefinition:
    """Return a validated immutable definition, or raise ContextCollabValidationError.

    This is a pure authoring operation, not an authenticated runtime write API.
    Preserve the supplied IDs, versions and publication strategy exactly. Validate
    the same structural rules as ProductCatalog; installation uniqueness and local
    capability negotiation remain separate. Do not mutate any existing catalog,
    create a Collet/Task/Method binding, or execute publication/admission operations.
    """
    definition = ProductProfileDefinition(
        ref=command.ref,
        composition=command.composition,
        scenario_roles=command.scenario_roles,
        domain=command.domain,
        workflow=command.workflow,
        governance=command.governance,
        compatibility=command.compatibility,
        publication=command.publication,
    )
    validate_product_profile(definition)
    return definition
