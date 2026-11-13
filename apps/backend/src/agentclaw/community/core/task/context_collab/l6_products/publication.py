"""Declarative Open publication policy; execution stays in application services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationType,
)


if TYPE_CHECKING:
    from agentclaw.community.core.task.context_collab.l6_products.contracts import (
        ScenarioRole,
    )


@dataclass(frozen=True)
class OpenAdmissionPolicy:
    """Shared Open participation values; a product owns its execution strategy."""

    applicant_role: str
    accountable_role: str
    participation_type: ParticipationType
    capacity: int

    def __post_init__(self) -> None:
        if any(
            not isinstance(role, str) or not role.strip()
            for role in (self.applicant_role, self.accountable_role)
        ):
            raise ContextCollabValidationError("admission requires scenario roles")
        if type(self.capacity) is not int or self.capacity < 1:
            raise ContextCollabValidationError("admission capacity must be positive")
        if not isinstance(self.participation_type, ParticipationType):
            raise ContextCollabValidationError("invalid Open participation type")

    def validate_roles(self, roles: tuple[ScenarioRole, ...]) -> None:
        role_map = {role.name: role for role in roles}
        for name in (self.applicant_role, self.accountable_role):
            role = role_map.get(name)
            if role is None or not role.required:
                raise ContextCollabValidationError(
                    "publication role must map to a required typed subject"
                )


@dataclass(frozen=True)
class OpenPublicationProfile:
    admission: OpenAdmissionPolicy
    dispatch_after: ApplicationState = ApplicationState.ADMITTED
    partial_failure: str = "propagate_without_rollback"

    def __post_init__(self) -> None:
        if (
            self.dispatch_after is not ApplicationState.ADMITTED
            or self.partial_failure != "propagate_without_rollback"
        ):
            raise ContextCollabValidationError(
                "unsupported publication failure/delivery policy"
            )

    def require_dispatch_ready(self, application_state: str) -> None:
        """Check L5's returned state; this does not create a product lifecycle."""
        if application_state != self.dispatch_after.value:
            raise ContextCollabValidationError(
                "Open publication dispatch requires an ADMITTED application"
            )
