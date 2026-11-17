"""Commission-owned, versioned designated-executor admission strategies."""

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
    policy = profile.composition.require_policy("Open").publication.admission
    if not isinstance(policy, DesignatedOpenAdmission):
        raise ContextCollabValidationError("unsupported designated admission policy")
    return policy


@dataclass(frozen=True)
class BotSelfApplicationAdmission(OpenAdmissionPolicy):
    """Designated Bot applies itself; owner policy automatically screens/admit.

    The accountable scenario role owns the publication policy; the application
    itself is accountable to the authenticated Bot owner.
    Participation belongs only to the task's Collet scope under Open. Neither
    designation nor discovery is admission or a Space/resource grant.
    """

    strategy: str = "designated_bot_self_application"

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            self.strategy != "designated_bot_self_application"
            or self.participation_type is not ParticipationType.COLLET_PARTICIPANT
            or self.capacity != 1
        ):
            raise ContextCollabValidationError(
                "unsupported Bot self-application policy"
            )

    def validate_roles(self, roles: tuple[ScenarioRole, ...]) -> None:
        super().validate_roles(roles)
        sources = {role.name: role.participant_source for role in roles}
        if (
            sources[self.applicant_role] != "executor_ref"
            or sources[self.accountable_role] != "principal_ref"
        ):
            raise ContextCollabValidationError(
                "self-application requires designated executor and owner"
            )
