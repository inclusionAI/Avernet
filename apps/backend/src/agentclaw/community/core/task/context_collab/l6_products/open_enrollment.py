"""L6 authoring refs and pre-Work enrollment Service API (local, not ACCS wire)."""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote, unquote

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    ParticipationFacts,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)


def scenario_role_ref(profile: VersionedProfileRef, role: str) -> str:
    """Reference a role requirement, never a participant-specific RoleBinding."""
    return f"profiles/{quote(profile.profile_id, safe='')}@{profile.version}/roles/{quote(role, safe='')}"


def scenario_role_name(profile: VersionedProfileRef, ref: str) -> str:
    """Decode only canonical role requirements scoped to this pinned profile."""
    prefix = scenario_role_ref(profile, "")
    if not isinstance(ref, str) or not ref.startswith(prefix):
        raise ContextCollabValidationError(
            "role requirement must reference the pinned profile"
        )
    role = unquote(ref[len(prefix) :])
    if not role.strip() or scenario_role_ref(profile, role) != ref:
        raise ContextCollabValidationError("invalid role requirement reference")
    return role


@dataclass(frozen=True)
class AdmittedAgent:
    application_ref: str
    agent_ref: AgentRef

    def __post_init__(self) -> None:
        if (
            not isinstance(self.application_ref, str)
            or not self.application_ref.strip()
        ):
            raise ContextCollabValidationError("application_ref must be non-empty")
        if not isinstance(self.agent_ref, AgentRef):
            raise ContextCollabValidationError("enrollment requires a typed AgentRef")
        AgentRef.from_dict(self.agent_ref.to_dict())


@dataclass(frozen=True)
class OpenEnrollmentCommand:
    collet_id: str
    expected_version: int
    admissions: tuple[AdmittedAgent, ...]
    executor_ref: AgentRef

    def __post_init__(self) -> None:
        if not isinstance(self.collet_id, str) or not self.collet_id.strip():
            raise ContextCollabValidationError("collet_id must be non-empty")
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise ContextCollabValidationError("expected_version must be positive")
        if (
            not isinstance(self.admissions, tuple)
            or not self.admissions
            or any(not isinstance(item, AdmittedAgent) for item in self.admissions)
        ):
            raise ContextCollabValidationError("enrollment requires typed admissions")
        if len({a.application_ref for a in self.admissions}) != len(
            self.admissions
        ) or len({a.agent_ref.agent_id for a in self.admissions}) != len(
            self.admissions
        ):
            raise ContextCollabValidationError(
                "duplicate enrollment subject or application"
            )
        if not isinstance(self.executor_ref, AgentRef) or self.executor_ref not in {
            a.agent_ref for a in self.admissions
        }:
            raise ContextCollabValidationError("executor must be among admitted Agents")


class ProductOpenEnrollmentServiceProtocol(Protocol):
    def bind(
        self, command: OpenEnrollmentCommand, context: ActorContext
    ) -> ParticipationFacts:
        """Bind a complete recruited team before root Work exists, without starting it.

        Only the Collet initiator may enroll, through injected G1 authorization.
        Every assignment must have current ADMITTED application, decision and exact
        scoped Collet-participation evidence under the pinned ACTIVE Open binding.
        All required deferred roles must be covered. A coordinator explicitly picks
        one admitted root executor; no identity is inferred from role order.
        SQL CAS appends one enrollment event; initial ProductInstance stays pinned.
        Repeated enrollment and post-Work rebinding fail. This is not Open.admit,
        does not grant resources or dispatch work, and is not a distributed transaction.
        """
        ...
