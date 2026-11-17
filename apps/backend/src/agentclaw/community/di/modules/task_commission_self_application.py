"""Bounded commission Open adapter: designated Bot discovery/apply and policy review.

SQL owns task/designation/profile; MethodStore owns admission/participant binding
evidence. They are not atomic or restart-durable together. No Space membership/grant
is written.
"""

from datetime import datetime, timezone
from uuid import uuid4

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    initial_executor_refs,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    ROLE_SELECTED_AGENT,
    RoleBinding,
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.single_profile import (
    SingleSelection,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    GroupMethodOpsServiceProtocol,
    SingleSelectCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    product_definition,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_method_kind,
    creation_profile_ref,
    creation_product_kind,
    creation_workflow_kind,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenScreenCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.products.task_commission.publication import (
    BotSelfApplicationAdmission,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    ApplyForTaskCommand,
    AssigneeAdmissionCommand,
    AssigneeAdmissionReceipt,
    TaskOpportunityView,
)
from agentclaw.community.di.modules.task_commission_method_ops_adapter import (
    TaskCommissionOpenMethodAdapter,
)

from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionEdgeState,
)
from agentclaw.community.core.task.context_collab.l6_products.topology import (
    product_method_binding_ref,
    product_relation_id,
)

_POLICY_REF = "task_commission.designated_bot_self_application/1"


class TaskCommissionSelfApplicationAdapter(TaskCommissionOpenMethodAdapter):
    """Bounded self-application, with explicit Single selection for composite profiles."""

    @inject
    def __init__(
        self,
        open_ops: OpenMethodOpsServiceProtocol,
        product_catalog: ProductCatalog,
        store: MethodStoreProtocol,
        repository: ContextCollabRepositoryProtocol,
        group_ops: GroupMethodOpsServiceProtocol,
    ) -> None:
        super().__init__(open_ops, product_catalog)
        self._store = store
        self._repo = repository
        self._single_ops = group_ops

    def _self_service_profile(
        self, ref: VersionedProfileRef
    ) -> ProductProfileDefinition:
        profile = self._product_catalog.get(ref.profile_id, ref.version)
        if (
            profile.ref.profile_id != "task_commission"
            or profile.composition.method_kind not in {"Open", "Open + Single"}
            or not isinstance(
                profile.composition.require_policy("Open").publication.admission,
                BotSelfApplicationAdmission,
            )
        ):
            raise ContextCollabValidationError(
                "profile does not support Bot self-application"
            )
        return profile

    @staticmethod
    def _selected_agent_role_refs(
        collab_context_id: str, profile: ProductProfileDefinition
    ) -> tuple[str, ...]:
        return tuple(
            role_binding_ref(
                collab_context_id,
                RoleBinding(
                    role=role.l4_role,
                    participant_id=f"participant_{role.participant_source}",
                ),
            )
            for role in profile.scenario_roles
            if role.l4_role == ROLE_SELECTED_AGENT
        )

    def publish_designated_opportunity(
        self, command: AssigneeAdmissionCommand, context: ActorContext
    ) -> str:
        profile = self._self_service_profile(command.product_profile_ref)
        record = self._repo.get_current_status(command.collab_context_id)
        if (
            record is None
            or record.collet.initiator_ref != context.actor
            or command.accountable_principal_id != context.actor.principal_id
            or record.designated_executor_id != command.assignee_agent_id
            or product_definition(
                product_kind=creation_product_kind(record.creation_facts),
                workflow_kind=creation_workflow_kind(record.creation_facts),
                method_kind=creation_method_kind(record.creation_facts),
                profile_ref=creation_profile_ref(record.creation_facts),
                catalog=self._product_catalog,
            )
            != profile
            or command.method_binding_ref
            != product_method_binding_ref(command.collab_context_id, profile, "Open")
        ):
            raise ContextCollabValidationError(
                "opportunity does not match owner/task selection"
            )
        policy = profile.composition.require_policy("Open").publication.admission
        return self._open_ops.publish_opportunity(
            OpenOpportunityCommand(
                collet_id=command.collab_context_id,
                actor_context=context,
                participation_type=policy.participation_type,
                capacity=policy.capacity,
                admission_policy_refs=[_POLICY_REF],
                reason="task_commission.owner_authorized_auto_admission",
            ),
        ).object_id

    @staticmethod
    def _designated(record: ContextStatusRecord, context: ActorContext) -> bool:
        refs = (
            record.task.executor_refs
            if record.task is not None
            else initial_executor_refs(record.events)
        )
        executor = refs[0] if len(refs) == 1 else None
        return (
            executor is not None
            and context.actor.principal_type == "bot"
            and (
                executor.agent_type == "bot"
                and executor.agent_id == context.actor.principal_id
            )
        )

    def query_opportunities(
        self, profile_ref: VersionedProfileRef, context: ActorContext
    ) -> tuple[TaskOpportunityView, ...]:
        profile = self._self_service_profile(profile_ref)
        policy = profile.composition.require_policy("Open").publication.admission
        if context.actor.principal_type != "bot" or not context.actor.principal_id:
            raise ContextCollabValidationError("opportunity discovery requires a Bot")
        axes = current_axes()
        opportunities = self._open_ops.query_opportunities(actor_context=context)
        views = []
        for collet, facts, _ in self._repo.list_collets_by_product(
            profile.ref.profile_id
        ):
            if creation_profile_ref(facts) != profile.ref:
                continue
            record = self._repo.get_current_status(collet.collet_id)
            if (
                record is None
                or not self._designated(record, context)
                or record.task is None
                or record.task.status != "created"
            ):
                continue
            binding_id = product_method_binding_ref(collet.collet_id, profile, "Open")
            binding = self._store.get_binding(axes, binding_id, collet.collet_id)
            if binding is None or binding.state is not BindingState.ACTIVE:
                continue
            for opportunity in opportunities:
                if (
                    opportunity.method_binding_id != binding_id
                    or opportunity.admission_policy_refs != (_POLICY_REF,)
                    or opportunity.scope_ref != collet.collet_id
                    or opportunity.participation_type
                    is not ParticipationType.COLLET_PARTICIPANT
                    or opportunity.capacity != policy.capacity
                ):
                    continue
                views.append(
                    TaskOpportunityView(
                        collet.collet_id,
                        opportunity.opportunity_id,
                        binding_id,
                        profile.ref,
                        record.task.objective,
                        opportunity.scope_ref,
                    )
                )
        return tuple(views)

    def apply_for_task(
        self, command: ApplyForTaskCommand, context: ActorContext
    ) -> AssigneeAdmissionReceipt:
        # Re-evaluate discovery policy; a known ref never bypasses designation,
        # scope, selected version, capacity, lifecycle or time-window checks.
        visible = self.query_opportunities(command.product_profile_ref, context)
        if (
            not any(
                item.collab_context_id == command.collab_context_id
                and item.opportunity_id == command.opportunity_id
                and item.method_binding_ref == command.method_binding_ref
                for item in visible
            )
            or not command.accountable_principal_id.strip()
        ):
            raise ContextCollabValidationError(
                "task opportunity unavailable to this Bot"
            )
        record = self._repo.get_current_status(command.collab_context_id)
        if record is None:
            raise ContextCollabValidationError("task not found")
        if (
            context.accountable_principal_ref is None
            or context.accountable_principal_ref.principal_id
            != command.accountable_principal_id
        ):
            raise ContextCollabValidationError(
                "application owner must match authenticated Bot owner"
            )
        submitted = self._open_ops.submit_application(
            OpenApplicationCommand(
                opportunity_ref=command.opportunity_id,
                actor_context=context,
                applicant_agent_ref=context.actor.principal_id,
                requested_role_refs=self._selected_agent_role_refs(
                    command.collab_context_id,
                    self._self_service_profile(command.product_profile_ref),
                ),
                reason="task_commission.bot_self_application",
            ),
        )
        return self.review_task_application(command, submitted.object_id, context)

    def review_task_application(
        self, command: ApplyForTaskCommand, application_id: str, context: ActorContext
    ) -> AssigneeAdmissionReceipt:
        """Continue the actual product policy after L5 submission, without reapply.

        Discovery cannot be reused here: the submitted application occupies the
        capacity-1 slot. Validate its stored facts and product eligibility instead.
        L5 already checked the application window/capacity at submission time.
        """
        profile = self._self_service_profile(command.product_profile_ref)
        record = self._repo.get_current_status(command.collab_context_id)
        policy = profile.composition.require_policy("Open").publication.admission
        axes, binding_id = current_axes(), command.method_binding_ref
        application = self._store.get_application(
            axes, application_id, binding_id, command.collab_context_id
        )
        opportunity = self._store.get_opportunity(
            axes, command.opportunity_id, binding_id, command.collab_context_id
        )
        binding = self._store.get_binding(axes, binding_id, command.collab_context_id)
        if (
            record is None
            or not self._designated(record, context)
            or record.task is None
            or record.task.status != "created"
            or product_definition(
                product_kind=creation_product_kind(record.creation_facts),
                workflow_kind=creation_workflow_kind(record.creation_facts),
                method_kind=creation_method_kind(record.creation_facts),
                profile_ref=creation_profile_ref(record.creation_facts),
                catalog=self._product_catalog,
            )
            != profile
            or binding_id
            != product_method_binding_ref(command.collab_context_id, profile, "Open")
            or binding is None
            or binding.state is not BindingState.ACTIVE
            or binding.method_names != ("Open",)
            or application is None
            or application.state is not ApplicationState.SUBMITTED
            or application.opportunity_ref != command.opportunity_id
            or application.applicant_agent_ref != context.actor.principal_id
            or not command.accountable_principal_id.strip()
            or application.accountable_principal_ref != command.accountable_principal_id
            or opportunity is None
            or opportunity.scope_ref != command.collab_context_id
            or opportunity.participation_type
            is not ParticipationType.COLLET_PARTICIPANT
            or opportunity.admission_policy_refs != (_POLICY_REF,)
            or opportunity.capacity != policy.capacity
        ):
            raise ContextCollabValidationError(
                "application unavailable for task review"
            )
        method_refs = self._require_active_methods(command.collab_context_id, profile)
        expected_roles = self._selected_agent_role_refs(
            command.collab_context_id, profile
        )
        if not expected_roles or application.requested_role_refs != expected_roles:
            raise ContextCollabValidationError("application roles do not match task")
        # The owner authorized this exact bounded rule when publishing the
        # opportunity. This is policy execution, not a forged inbound Bot/user.
        policy_context = ActorContext(
            record.collet.initiator_ref, source="product_policy"
        )
        self._open_ops.screen(
            command=OpenScreenCommand(
                application_ref=application_id,
                context=policy_context,
                collect_id=command.collab_context_id,
                reason=_POLICY_REF,
            ),
        )
        decision_id = f"dec_{uuid4().hex[:12]}"
        admitted = self._open_ops.admit(
            command=OpenAdmitCommand(
                application_ref=application_id,
                context=policy_context,
                collect_id=command.collab_context_id,
                decision_id=decision_id,
                reason=_POLICY_REF,
            ),
        )
        decision = self._store.get_decision(
            current_axes(), decision_id, binding_id, command.collab_context_id
        )
        if decision is None or len(decision.resulting_binding_refs) != 1:
            raise ContextCollabValidationError(
                "admission did not establish a scoped binding"
            )
        if "Single" in method_refs:
            # Admission establishes a scoped participant, not an executor selection.
            # Keep this product composition outside the reusable L5 Open service.
            self._single_ops.select(
                command.collab_context_id,
                method_refs["Single"],
                SingleSelectCommand(
                    agent_participant_ref=decision.resulting_binding_refs[0],
                    scope_ref=command.collab_context_id,
                    selection_evidence_refs=[application_id, decision_id],
                    selection_id=f"sel_{application_id}",
                    reason="task_commission.select_admitted_assignee",
                ),
                policy_context,
            )
        self.require_admitted(command.collab_context_id, context)
        return AssigneeAdmissionReceipt(
            command.opportunity_id,
            application_id,
            decision_id,
            admitted.state,
            decision.admitted_scope_ref,
            decision.resulting_binding_refs,
        )

    def _require_active_methods(
        self, collet_id: str, profile: ProductProfileDefinition
    ) -> dict[str, str]:
        axes, refs = current_axes(), {}
        for method in profile.composition.required_methods:
            name = method.method_kind
            binding_id = product_method_binding_ref(collet_id, profile, name)
            binding = self._store.get_binding(axes, binding_id, collet_id)
            if (
                binding is None
                or binding.state is not BindingState.ACTIVE
                or binding.method_names != (name,)
                or binding.profile_versions != {name: method.profile_version}
            ):
                raise ContextCollabValidationError(
                    f"execution requires an active {name} Method binding matching the pinned product"
                )
            refs[name] = binding_id
        for relation in profile.composition.relations:
            edge = self._store.get_composition_edge(
                axes, product_relation_id(collet_id, relation), collet_id
            )
            if (
                edge is None
                or edge.state is not CompositionEdgeState.ACTIVE
                or edge.parent_binding != refs[relation.parent_method]
                or edge.child_binding != refs[relation.child_method]
                or edge.relation_kind != relation.relation
            ):
                raise ContextCollabValidationError(
                    "execution requires the active product Method composition"
                )
        return refs

    @staticmethod
    def _selection_is_current(selection: SingleSelection) -> bool:
        try:
            starts = datetime.fromisoformat(selection.valid_from)
            ends = (
                datetime.fromisoformat(selection.valid_until)
                if selection.valid_until is not None
                else None
            )
            now = datetime.now(timezone.utc)
            return starts <= now and (ends is None or now < ends)
        except (ValueError, TypeError):
            # Invalid or timezone-naive evidence never authorizes Work.
            return False

    def require_admitted(self, collab_context_id: str, context: ActorContext) -> None:
        record = self._repo.get_current_status(collab_context_id)
        if record is None or not self._designated(record, context):
            raise ContextCollabValidationError("execution requires the designated Bot")
        profile = product_definition(
            product_kind=creation_product_kind(record.creation_facts),
            workflow_kind=creation_workflow_kind(record.creation_facts),
            method_kind=creation_method_kind(record.creation_facts),
            profile_ref=creation_profile_ref(record.creation_facts),
            catalog=self._product_catalog,
        )
        if profile is None:
            raise ContextCollabValidationError("task has no product profile")
        method_refs = self._require_active_methods(collab_context_id, profile)
        axes, binding_id = current_axes(), method_refs["Open"]
        applications = {
            app.application_id: app
            for app in self._store.list_applications(
                axes, binding_id, collab_context_id
            )
        }
        participants = {
            participant.participant_binding_id: participant
            for participant in self._store.list_participant_bindings(
                axes, binding_id, collab_context_id
            )
        }
        expected_roles = self._selected_agent_role_refs(collab_context_id, profile)
        for decision in self._store.list_decisions(axes, binding_id, collab_context_id):
            app = applications.get(decision.application_ref)
            if (
                app is None
                or app.state is not ApplicationState.ADMITTED
                or app.applicant_agent_ref != context.actor.principal_id
                or decision.admitted_scope_ref != collab_context_id
            ):
                continue
            opportunity = self._store.get_opportunity(
                axes, app.opportunity_ref, binding_id, collab_context_id
            )
            if (
                opportunity is None
                or opportunity.scope_ref != collab_context_id
                or opportunity.participation_type
                is not ParticipationType.COLLET_PARTICIPANT
                or opportunity.admission_policy_refs != (_POLICY_REF,)
            ):
                continue
            admitted_participants = {
                ref
                for ref in decision.resulting_binding_refs
                if ref in participants
                and participants[ref].scope_ref == collab_context_id
                and participants[ref].participant_ref == context.actor.principal_id
                and participants[ref].role_binding_refs == expected_roles
                and app.requested_role_refs == expected_roles
            }
            if admitted_participants:
                if "Single" in method_refs:
                    selection = self._store.active_selection(
                        axes,
                        method_refs["Single"],
                        collab_context_id,
                        collab_context_id,
                    )
                    if (
                        selection is None
                        or selection.selected_agent_participant_ref
                        not in admitted_participants
                        or not {app.application_id, decision.admission_decision_id}
                        <= set(selection.selection_evidence_refs)
                        or selection.selected_by_ref != record.owner_principal_id
                        or not self._selection_is_current(selection)
                    ):
                        raise ContextCollabValidationError(
                            "execution requires an active Single selection "
                            "for the admitted participant"
                        )
                return
        raise ContextCollabValidationError(
            "execution requires an admitted task-scoped participation binding"
        )
