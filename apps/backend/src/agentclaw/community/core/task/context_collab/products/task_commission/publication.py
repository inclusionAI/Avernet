"""Commission-owned, versioned designated-executor admission strategies."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    BotSelfApplicationAdmission as BotSelfApplicationAdmission,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    LegacyProductConfiguration,
)


# Identity reexport; the serializable admission value is owned by L5.
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    DesignatedOpenAdmission as DesignatedOpenAdmission,
)


def require_designated_admission(
    profile: LegacyProductConfiguration,
) -> DesignatedOpenAdmission:
    """The bounded executor must not silently interpret another product's strategy."""
    policy = profile.composition.require_policy("Open").publication.admission
    if not isinstance(policy, DesignatedOpenAdmission):
        raise ContextCollabValidationError("unsupported designated admission policy")
    return policy
