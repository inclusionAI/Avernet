"""L5 Open publication/admission values; callers own product orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationType,
)


class AdmissionRole(Protocol):
    """Read-only role mapping consumed by L5 without importing L6 ScenarioRole."""

    @property
    def name(self) -> str: ...

    @property
    def required(self) -> bool: ...

    @property
    def participant_source(self) -> str: ...


@dataclass(frozen=True)
class OpenAdmissionPolicy:
    """Shared Open participation constraints, independent of product definitions."""

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

    def validate_roles(self, roles: tuple[AdmissionRole, ...]) -> None:
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

    def validate_roles(self, roles: tuple[AdmissionRole, ...]) -> None:
        super().validate_roles(roles)
        sources = {role.name: role.participant_source for role in roles}
        if (
            sources[self.applicant_role] != "executor_ref"
            or sources[self.accountable_role] != "principal_ref"
        ):
            raise ContextCollabValidationError(
                "self-application requires designated executor and owner"
            )
