"""Bounded commission Open adapter: designated Bot discovery/apply and policy review.

SQL owns task/designation/profile; MethodStore owns admission/member state. They
are not atomic or restart-durable together. No Space membership/grant is written.
"""

from uuid import uuid4

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    MemberState,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _now,
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    product_definition,
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
    OpenDecisionOpCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
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

_POLICY_REF = "task_commission.designated_bot_self_application/1"


class TaskCommissionSelfApplicationAdapter(TaskCommissionOpenMethodAdapter):
    """Extends, rather than redefines, v1's publisher-mediated compatibility path."""

    @inject
    def __init__(
        self,
        open_ops: OpenMethodOpsServiceProtocol,
        product_catalog: ProductCatalog,
        store: MethodStoreProtocol,
        repository: ContextCollabRepositoryProtocol,
    ) -> None:
        super().__init__(open_ops, product_catalog)
        self._store = store
        self._repo = repository

    def _self_service_profile(
        self, ref: VersionedProfileRef
    ) -> ProductProfileDefinition:
        profile = self._product_catalog.get(ref.profile_id, ref.version)
        if (
            profile.ref.profile_id != "task_commission"
            or profile.composition.method_kind != "Open + Group"
            or not isinstance(
                profile.require_publication().admission, BotSelfApplicationAdmission
            )
        ):
            raise ContextCollabValidationError(
                "profile does not support Bot self-application"
            )
        return profile

    def publish_designated_opportunity(
        self, command: AssigneeAdmissionCommand, context: ActorContext
    ) -> str:
        profile = self._self_service_profile(command.product_profile_ref)
        record = self._repo.get_current_status(command.collab_context_id)
        if (
            record is None
            or record.context_spec.principal_ref != context.actor
            or command.accountable_principal_id != context.actor.principal_id
            or record.designated_executor_id != command.assignee_agent_id
            or product_definition(record.context_spec, self._product_catalog) != profile
            or command.method_binding_ref
            != top_level_binding_id(command.collab_context_id)
        ):
            raise ContextCollabValidationError(
                "opportunity does not match owner/task selection"
            )
        policy = profile.require_publication().admission
        return self._open_ops.publish_opportunity(
            command.collab_context_id,
            command.method_binding_ref,
            OpenOpportunityCommand(
                scope_ref=command.collab_context_id,
                participation_type=policy.participation_type.value,
                capacity=policy.capacity,
                opens_at=_now(),
                admission_policy_refs=[_POLICY_REF],
                reason="task_commission.owner_authorized_auto_admission",
            ),
            context,
        ).object_id

    @staticmethod
    def _designated(record: ContextStatusRecord, context: ActorContext) -> bool:
        executor = record.context_spec.executor_ref
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
        if context.actor.principal_type != "bot" or not context.actor.principal_id:
            raise ContextCollabValidationError("opportunity discovery requires a Bot")
        axes = current_axes()
        views = []
        for collet, spec, _ in self._repo.list_collets_by_product(
            profile.ref.profile_id
        ):
            if spec.product_profile_version != profile.ref.version:
                continue
            record = self._repo.get_current_status(collet.collet_id)
            if (
                record is None
                or not self._designated(record, context)
                or record.task is None
                or record.task.status != "created"
            ):
                continue
            binding_id = top_level_binding_id(collet.collet_id)
            binding = self._store.get_binding(axes, binding_id, collet.collet_id)
            if binding is None or binding.state is not BindingState.ACTIVE:
                continue
            for opportunity in self._open_ops.query_opportunities(
                collet.collet_id, binding_id, context
            ):
                if (
                    opportunity.admission_policy_refs != (_POLICY_REF,)
                    or opportunity.scope_ref != collet.collet_id
                    or opportunity.participation_type
                    is not ParticipationType.GROUP_MEMBER
                    or opportunity.capacity
                    != profile.require_publication().admission.capacity
                ):
                    continue
                views.append(
                    TaskOpportunityView(
                        collet.collet_id,
                        opportunity.opportunity_id,
                        binding_id,
                        profile.ref,
                        spec.objective,
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
        binding_id = command.method_binding_ref
        submitted = self._open_ops.submit_application(
            command.collab_context_id,
            binding_id,
            command.opportunity_id,
            OpenApplicationCommand(
                applicant_agent_ref=context.actor.principal_id,
                accountable_principal_ref=command.accountable_principal_id,
                requested_role_refs=[
                    role_binding_ref(command.collab_context_id, role)
                    for role in record.context_spec.role_bindings
                    if role.role == "selected-agent"
                ],
                reason="task_commission.bot_self_application",
            ),
            context,
        )
        # The owner authorized this exact bounded rule when publishing the
        # opportunity. This is policy execution, not a forged inbound Bot/user.
        policy_context = ActorContext(
            record.context_spec.principal_ref, source="product_policy"
        )
        self._open_ops.screen(
            command.collab_context_id,
            binding_id,
            submitted.object_id,
            OpenDecisionOpCommand(reason=_POLICY_REF),
            policy_context,
        )
        decision_id = f"dec_{uuid4().hex[:12]}"
        admitted = self._open_ops.admit(
            command.collab_context_id,
            binding_id,
            submitted.object_id,
            OpenAdmitCommand(
                admitted_scope_ref=command.collab_context_id,
                decision_id=decision_id,
                reason=_POLICY_REF,
            ),
            policy_context,
        )
        decision = self._store.get_decision(
            current_axes(), decision_id, binding_id, command.collab_context_id
        )
        if decision is None or not decision.resulting_binding_refs:
            raise ContextCollabValidationError(
                "admission did not establish a scoped binding"
            )
        self.require_admitted(command.collab_context_id, context)
        return AssigneeAdmissionReceipt(
            command.opportunity_id,
            submitted.object_id,
            decision_id,
            admitted.state,
            decision.admitted_scope_ref,
            decision.resulting_binding_refs,
        )

    def require_admitted(self, collab_context_id: str, context: ActorContext) -> None:
        record = self._repo.get_current_status(collab_context_id)
        if record is None or not self._designated(record, context):
            raise ContextCollabValidationError("execution requires the designated Bot")
        axes = current_axes()
        binding_id = top_level_binding_id(collab_context_id)
        binding = self._store.get_binding(axes, binding_id, collab_context_id)
        if binding is None or binding.state is not BindingState.ACTIVE:
            raise ContextCollabValidationError(
                "execution requires an active Method binding"
            )
        applications = {
            app.application_id: app
            for app in self._store.list_applications(
                axes, binding_id, collab_context_id
            )
        }
        members = {
            member.group_member_binding_id: member
            for member in self._store.list_members(axes, binding_id, collab_context_id)
        }
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
                or opportunity.admission_policy_refs != (_POLICY_REF,)
            ):
                continue
            if any(
                ref in members
                and members[ref].state is MemberState.ACTIVE
                and members[ref].participant_ref == context.actor.principal_id
                for ref in decision.resulting_binding_refs
            ):
                return
        raise ContextCollabValidationError(
            "execution requires an admitted task-scoped participation binding"
        )
