"""Commission-owned publisher-mediated designated-executor admission strategy."""

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
    ScenarioRole,
)
from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenAdmissionPolicy,
)


@dataclass(frozen=True)
class DesignatedOpenAdmission(OpenAdmissionPolicy):
    """Publisher-mediated admission, not applicant impersonation or a resource grant.

    Only this bounded strategy is implemented. A different contribution kind or
    step sequence needs a matching executor, not a silently ignored template.
    """

    strategy: str = "publisher_mediated"
    steps: tuple[str, ...] = (
        "publish_opportunity",
        "submit_application",
        "screen",
        "admit",
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            self.strategy != "publisher_mediated"
            or self.participation_type is not ParticipationType.SINGLE_CONTRIBUTION
            or self.steps
            != ("publish_opportunity", "submit_application", "screen", "admit")
        ):
            raise ContextCollabValidationError("unsupported Open publication strategy")

    def validate_roles(self, roles: tuple[ScenarioRole, ...]) -> None:
        super().validate_roles(roles)
        role_map = {role.name: role for role in roles}
        for name, source in (
            (self.applicant_role, "executor_ref"),
            (self.accountable_role, "principal_ref"),
        ):
            if role_map[name].participant_source != source:
                raise ContextCollabValidationError(
                    "designated admission requires executor and principal subjects"
                )


def require_designated_admission(
    profile: ProductProfileDefinition,
) -> DesignatedOpenAdmission:
    """The bounded executor must not silently interpret another product's strategy."""
    policy = profile.require_publication().admission
    if not isinstance(policy, DesignatedOpenAdmission):
        raise ContextCollabValidationError("unsupported designated admission policy")
    return policy
