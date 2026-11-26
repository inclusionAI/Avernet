"""Read-only correlation of Open admission and retained runtime role evidence."""

from typing import Sequence

from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    ScopedParticipantBinding,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    ADMITTED_BINDING_EVENT,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    AdmissionDecision,
    OpenOpportunity,
    ParticipationApplication,
    ApplicationState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)


def require_admission_evidence(
    store: MethodStoreProtocol,
    collet_id: str,
    binding_id: str,
    application_ref: str,
    agent_id: str,
) -> tuple[
    ParticipationApplication,
    OpenOpportunity,
    AdmissionDecision,
    ScopedParticipantBinding,
]:
    """Neither an application state nor a scoped binding alone grants authority."""
    axes = current_axes()
    binding = store.get_binding(axes, binding_id, collet_id)
    if (
        binding is None
        or "Open" not in binding.method_names
        or binding.state is not BindingState.ACTIVE
    ):
        raise ContextCollabValidationError("enrollment requires an ACTIVE Open binding")
    application = store.get_application(axes, application_ref, binding_id, collet_id)
    if (
        application is None
        or application.state is not ApplicationState.ADMITTED
        or application.applicant_agent_ref != agent_id
    ):
        raise ContextCollabValidationError(
            "enrollment requires a matching ADMITTED application"
        )
    opportunity = store.get_opportunity(
        axes, application.opportunity_ref, binding_id, collet_id
    )
    if (
        opportunity is None
        or opportunity.scope_ref != collet_id
        or opportunity.participation_type is not ParticipationType.COLLET_PARTICIPANT
    ):
        raise ContextCollabValidationError(
            "enrollment requires scoped COLLET_PARTICIPANT admission"
        )
    decisions = [
        d
        for d in store.list_decisions(axes, binding_id, collet_id)
        if d.application_ref == application.application_id
        and d.admitted_scope_ref == collet_id
    ]
    if len(decisions) != 1 or len(decisions[0].resulting_binding_refs) != 1:
        raise ContextCollabValidationError(
            "enrollment requires one correlated admission decision"
        )
    decision = decisions[0]
    participant = store.get_participant_binding(
        axes, decision.resulting_binding_refs[0], binding_id, collet_id
    )
    if (
        participant is None
        or participant.scope_ref != collet_id
        or participant.participant_ref != agent_id
        or participant.role_binding_refs != application.requested_role_refs
    ):
        raise ContextCollabValidationError(
            "enrollment requires matching scoped participation evidence"
        )
    return application, opportunity, decision, participant


def validate_enrolled_participation(
    collet_id: str, events: Sequence[EventRecord], store: MethodStoreProtocol
) -> None:
    """Recheck live admission before Swarm planning, execution and worker reports.

    Fixed-team Swarms have no enrollment event and retain existing behavior.
    Retained L4 role evidence cannot revive a revoked application or inactive Open.
    This does not make the SQL and Method stores a distributed transaction.
    """
    enrollments = [e for e in events if e.event_kind == ADMITTED_BINDING_EVENT]
    if len(enrollments) > 1:
        raise ContextCollabValidationError("conflicting Open enrollment evidence")
    for event in enrollments:
        payload = event.payload
        for evidence in payload["admissions"]:
            _, _, decision, participant = require_admission_evidence(
                store,
                collet_id,
                payload["open_binding_ref"],
                evidence["application_ref"],
                evidence["agent_ref"]["agent_id"],
            )
            if (
                decision.admission_decision_id != evidence["decision_ref"]
                or participant.participant_binding_id
                != evidence["participant_binding_ref"]
            ):
                raise ContextCollabValidationError(
                    "enrollment admission evidence changed"
                )
