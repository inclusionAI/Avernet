"""L4 participation facts, persisted as Event evidence rather than L6 authority."""

from dataclasses import dataclass
from typing import Sequence

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
    RoleBinding,
)

from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    normalize_executor_refs,
)

INITIAL_BINDING_EVENT = "task.participants_bound"
COLLET_BINDING_EVENT = "collet.participants_bound"


@dataclass(frozen=True)
class ParticipationFacts:
    participants: tuple[Participant, ...] = ()
    role_bindings: tuple[RoleBinding, ...] = ()

    def __post_init__(self) -> None:
        for field, cls in (
            ("participants", Participant),
            ("role_bindings", RoleBinding),
        ):
            values = getattr(self, field)
            if not isinstance(values, (list, tuple)) or any(
                not isinstance(v, cls) for v in values
            ):
                raise ContextCollabValidationError("participation requires typed facts")
            object.__setattr__(self, field, tuple(values))
        # Reuse typed decoders even for programmatically supplied facts.
        for participant in self.participants:
            Participant.from_dict(participant.to_dict())
        ids = [p.participant_id for p in self.participants]
        subjects = [(p.principal_ref, p.agent_ref) for p in self.participants]
        if len(set(ids)) != len(ids) or len(set(subjects)) != len(subjects):
            raise ContextCollabValidationError("duplicate Participant identity")
        if len(set(self.role_bindings)) != len(self.role_bindings):
            raise ContextCollabValidationError("duplicate RoleBinding")
        for binding in self.role_bindings:
            RoleBinding.from_dict(binding.to_dict())
            if binding.participant_id not in ids:
                raise ContextCollabValidationError(
                    "RoleBinding references a missing Participant"
                )

    def to_dict(self) -> dict:
        return {
            "participants": [p.to_dict() for p in self.participants],
            "role_bindings": [b.to_dict() for b in self.role_bindings],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ParticipationFacts":
        if not isinstance(data, dict) or set(data) != {"participants", "role_bindings"}:
            raise ContextCollabValidationError("invalid participation facts")
        for key in data:
            if not isinstance(data[key], list) or any(
                not isinstance(v, dict) for v in data[key]
            ):
                raise ContextCollabValidationError(
                    "invalid participation fact collection"
                )
        return cls(
            tuple(Participant.from_dict(p) for p in data["participants"]),
            tuple(RoleBinding.from_dict(b) for b in data["role_bindings"]),
        )


def initial_participation(events: Sequence[EventRecord]) -> ParticipationFacts:
    evidence = [
        e
        for e in events
        if e.event_kind in {"task.created", INITIAL_BINDING_EVENT, COLLET_BINDING_EVENT}
        and e.payload is not None
        and "participation" in e.payload
    ]
    if len(evidence) > 1:
        raise ContextCollabValidationError("conflicting initial participation evidence")
    return (
        ParticipationFacts.from_dict(evidence[0].payload["participation"])
        if evidence
        else ParticipationFacts()
    )


def initial_executor_refs(events: Sequence[EventRecord]) -> tuple[AgentRef, ...]:
    """Initial Task assignees declared by the actual Collet binding evidence."""

    facts = initial_participation(events)
    evidence = next((e for e in events if e.event_kind == COLLET_BINDING_EVENT), None)
    if evidence is None:
        return ()
    raw = (evidence.payload or {}).get("executor_refs")
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ContextCollabValidationError("invalid initial executor binding evidence")
    refs = normalize_executor_refs(tuple(AgentRef.from_dict(item) for item in raw))
    if any(ref not in {p.agent_ref for p in facts.participants} for ref in refs):
        raise ContextCollabValidationError("initial executor is not a Participant")
    return refs


ADMITTED_BINDING_EVENT = "collet.admitted_participants_bound"


def current_participation(events: Sequence[EventRecord]) -> ParticipationFacts:
    """Initial facts plus an authorized, pre-Work Open enrollment.

    Enrollment is append-only evidence, not a rewrite of pinned product creation.
    Only the enrollment operation writes this internal event; generic append does
    not register it. Rebinding/reassignment and post-Work enrollment are unsupported.
    """
    initial = initial_participation(events)
    enrollments = [e for e in events if e.event_kind == ADMITTED_BINDING_EVENT]
    if len(enrollments) > 1:
        raise ContextCollabValidationError("conflicting Open enrollment evidence")
    if not enrollments:
        return initial
    added = ParticipationFacts.from_dict(enrollments[0].payload["participation"])
    return ParticipationFacts(
        initial.participants + added.participants,
        initial.role_bindings + added.role_bindings,
    )


def bound_executor_refs(events: Sequence[EventRecord]) -> tuple[AgentRef, ...]:
    """Work inherits runtime enrollment, or unchanged initial fixed assignments."""
    initial = initial_executor_refs(events)
    enrollments = [e for e in events if e.event_kind == ADMITTED_BINDING_EVENT]
    if not enrollments:
        return initial
    facts = current_participation(events)
    raw = enrollments[0].payload.get("executor_refs")
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ContextCollabValidationError("invalid enrolled executor evidence")
    refs = normalize_executor_refs(tuple(AgentRef.from_dict(item) for item in raw))
    holders = {b.participant_id for b in facts.role_bindings if b.role == "selected-agent"}
    if initial or len(refs) != 1 or not any(
        p.agent_ref == refs[0] and p.participant_id in holders for p in facts.participants
    ):
        raise ContextCollabValidationError("enrolled executor requires its RoleBinding")
    return refs
