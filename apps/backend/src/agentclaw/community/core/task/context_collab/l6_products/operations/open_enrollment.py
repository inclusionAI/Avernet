"""Materialize a recruited team's L4 facts from authoritative Open admission.

This bounded operation binds once, before root Work, and never changes immutable
ProductInstance creation refs. SQL event CAS and MethodStore admission remain
separate authorities; no distributed transaction or identity registry is implied.
"""

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.g2_keep_reliable_live.reliability import (
    ensure_expected_version,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendEventRecordRequest,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
    RoleBinding,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    ADMITTED_BINDING_EVENT,
    ParticipationFacts,
    initial_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.participation import (
    require_admission_evidence,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    agent_participant_id,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.open_enrollment import (
    AdmittedAgent,
    OpenEnrollmentCommand,
    ProductOpenEnrollmentServiceProtocol,
    scenario_role_ref,
    scenario_role_name,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.records import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)


def enrollment_payload(
    command: OpenEnrollmentCommand,
    record: ContextStatusRecord,
    profile: ProductProfileDefinition,
    store: MethodStoreProtocol,
) -> tuple[MethodBinding, dict]:
    """Validate current, correlated admission; return exact materialization evidence."""
    if record.product_instance is None:
        raise ContextCollabValidationError("enrollment requires an installed product")
    axes = current_axes()
    bindings = [
        b
        for b in store.list_bindings(axes, command.collet_id)
        if "Open" in b.method_names
        and b.method_binding_id in record.product_instance.method_binding_refs
    ]
    if len(bindings) != 1 or bindings[0].state is not BindingState.ACTIVE:
        raise ContextCollabValidationError(
            "enrollment requires one pinned ACTIVE Open binding"
        )
    binding = bindings[0]
    binding_id = binding.method_binding_id
    roles = {
        scenario_role_ref(profile.ref, r.name): r
        for r in profile.scenario_roles
        if r.participant_source == "open_admission" and r.l4_role != "selected-agent"
    }
    executors = [
        r
        for r in profile.scenario_roles
        if r.l4_role == "selected-agent" and r.participant_source == "open_admission"
    ]
    if len(executors) != 1:
        raise ContextCollabValidationError(
            "enrollment requires a deferred selected-agent role"
        )
    participants, assigned, evidence = [], [], []
    for admission in command.admissions:
        agent = admission.agent_ref
        profile.participant_scope.require(agent)
        application, opportunity, decision, participant = require_admission_evidence(
            store,
            command.collet_id,
            binding_id,
            admission.application_ref,
            agent.agent_id,
        )
        participant_ref = participant.participant_binding_id
        requested = application.requested_role_refs
        if (
            not requested
            or (roles and not set(requested) <= roles.keys())
            or not set(requested) <= set(opportunity.requirement_refs)
        ):
            raise ContextCollabValidationError(
                "application must request published, declared deferred roles"
            )
        pid = agent_participant_id(agent, command.executor_ref)
        participants.append(Participant(pid, None, agent))
        for ref in requested:
            role = roles[ref].l4_role if roles else scenario_role_name(profile.ref, ref)
            # Recruitment requirements cannot mint product ownership or the
            # designated executor; those are bound through their typed sources.
            protected = {
                r.l4_role
                for r in profile.scenario_roles
                if r.participant_source != "open_admission"
                or r.l4_role == "selected-agent"
            }
            if role in protected:
                raise ContextCollabValidationError(
                    "admission cannot assign a protected product role"
                )
            assigned.append(RoleBinding(role, pid))
        evidence.append(
            {
                "application_ref": application.application_id,
                "agent_ref": agent.to_dict(),
                "decision_ref": decision.admission_decision_id,
                "participant_binding_ref": participant_ref,
            }
        )
    assigned.append(
        RoleBinding(
            "selected-agent",
            agent_participant_id(command.executor_ref, command.executor_ref),
        )
    )
    required = {
        r.l4_role
        for r in profile.scenario_roles
        if r.participant_source == "open_admission" and r.required
    }
    if not required <= {b.role for b in assigned}:
        raise ContextCollabValidationError(
            "enrollment is missing required deferred roles"
        )
    added = ParticipationFacts(tuple(participants), tuple(assigned))
    initial = initial_participation(record.events)
    # Validate the complete result before writing (including duplicate subjects).
    ParticipationFacts(
        initial.participants + added.participants,
        initial.role_bindings + added.role_bindings,
    )
    return binding, {
        "participation": added.to_dict(),
        "executor_refs": [command.executor_ref.to_dict()],
        "open_binding_ref": binding_id,
        "admissions": evidence,
    }


def require_current_enrollment(
    record: ContextStatusRecord, catalog: ProductCatalog, store: MethodStoreProtocol
) -> None:
    """A retained binding event is evidence, not authority after admission revocation."""
    events = [e for e in record.events if e.event_kind == ADMITTED_BINDING_EVENT]
    if not events:
        return
    if len(events) != 1 or record.product_instance is None:
        raise ContextCollabValidationError("invalid Open enrollment evidence")
    payload = events[0].payload
    ref = record.product_instance.profile_ref
    command = OpenEnrollmentCommand(
        record.collet.collet_id,
        record.collet.version,
        tuple(
            AdmittedAgent(a["application_ref"], AgentRef.from_dict(a["agent_ref"]))
            for a in payload["admissions"]
        ),
        AgentRef.from_dict(payload["executor_refs"][0]),
    )
    _, expected = enrollment_payload(
        command, record, catalog.get(ref.profile_id, ref.version), store
    )
    if payload != expected:
        raise ContextCollabValidationError(
            "Open enrollment no longer matches admission evidence"
        )


class ProductOpenEnrollmentService(ProductOpenEnrollmentServiceProtocol):
    @inject
    def __init__(
        self,
        repository: ContextCollabRepositoryProtocol,
        store: MethodStoreProtocol,
        catalog: ProductCatalog,
        authorizer: MethodOpsAuthorizerProtocol,
    ) -> None:
        self.repository, self.store, self.catalog, self.authorizer = (
            repository,
            store,
            catalog,
            authorizer,
        )

    def bind(
        self, command: OpenEnrollmentCommand, context: ActorContext
    ) -> ParticipationFacts:
        record = self.repository.get_current_status(command.collet_id)
        if record is None:
            raise ContextCollabNotFoundError("enrollment Collet not found")
        if context.actor != record.collet.initiator_ref:
            raise ContextCollabValidationError(
                "only the Collet initiator may enroll participants"
            )
        ensure_expected_version(command.expected_version, record.collet.version)
        if record.task is not None or any(
            e.event_kind == ADMITTED_BINDING_EVENT for e in record.events
        ):
            raise ContextCollabValidationError(
                "enrollment requires unbound, pre-Work participants"
            )
        if record.product_instance is None:
            raise ContextCollabValidationError(
                "enrollment requires an installed product"
            )
        ref = record.product_instance.profile_ref
        binding, payload = enrollment_payload(
            command, record, self.catalog.get(ref.profile_id, ref.version), self.store
        )
        self.authorizer.check_open_op(
            "admit",
            binding,
            context,
            principal_id=record.collet.initiator_ref.principal_id,
            executor_id=None,
        )
        self.repository.append_event(
            command.collet_id,
            AppendEventRecordRequest(
                expected_version=command.expected_version,
                event_kind=ADMITTED_BINDING_EVENT,
                primitive_type="Collet",
                actor_ref=context.actor,
                parent_event=None,
                visibility="public",
                payload=payload,
            ),
        )
        return ParticipationFacts.from_dict(payload["participation"])
