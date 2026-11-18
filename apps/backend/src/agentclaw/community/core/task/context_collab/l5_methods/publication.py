"""L5 Open publication/admission values; callers own product orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationType,
)


_DECISION_ACTORS = frozenset({"accountable_role", "configured_agent"})
_DECISION_RULES = frozenset({"product_policy", "capability_evidence"})


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
    # ``accountable_role`` is the compatibility selector. A configured Agent
    # is selected with ``configured_agent`` and an exact AgentRef.
    decision_actor: str = "accountable_role"
    # Ref is intentionally absent for the accountable-role selector and exact
    # for the configured-Agent selector.
    decision_agent_ref: AgentRef | None = None
    # The rule is a bounded strategy identifier, not an inline algorithm.
    decision_rule: str = "product_policy"

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
        for name, value in (
            ("decision_actor", self.decision_actor),
            ("decision_rule", self.decision_rule),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ContextCollabValidationError(
                    f"admission {name} must not be empty"
                )
        if self.decision_actor not in _DECISION_ACTORS:
            raise ContextCollabValidationError("unsupported admission decision actor")
        if self.decision_rule not in _DECISION_RULES:
            raise ContextCollabValidationError("unsupported admission decision rule")
        if self.decision_actor == "configured_agent":
            if not isinstance(self.decision_agent_ref, AgentRef):
                raise ContextCollabValidationError(
                    "configured admission actor requires an AgentRef"
                )
        elif self.decision_agent_ref is not None:
            raise ContextCollabValidationError(
                "accountable admission actor must omit decision_agent_ref"
            )

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
    """Designated Bot applies itself; the configured admission actor decides.

    The default ``accountable_role`` actor preserves owner-policy screening and
    admission. ``configured_agent`` delegates only that decision to an exact
    AgentRef. The application itself is accountable to the authenticated Bot
    owner.
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
