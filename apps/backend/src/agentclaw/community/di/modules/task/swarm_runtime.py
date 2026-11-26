"""Composition root for product-selected Swarm planning; no service locator in L5."""

from dataclasses import dataclass
from collections.abc import Mapping
from agentclaw.community.core.task.context_collab.l6_products.profile_publication import (
    ProductProfilePublicationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.workflow_planning import (
    WorkflowPlanningService,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow_ports import (
    SwarmWorkflowPolicyReader,
    SwarmArtifactEvidenceValidator,
    UnavailableSwarmArtifactValidator,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.product_profile_publication import (
    ProductProfilePublicationService,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProductProfilePublicationAuthorizer,
    ProfilePublisherAllowlist,
)
from agentclaw.community.core.task.context_collab.l6_products.topology import (
    product_binding_refs,
)

from injector import Module, Binder, inject, provider, singleton

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.execution_report import (
    StageReportProtocol,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    current_participation,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    EventWriter,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.unavailable_execution import (
    UnavailableStageExecutionService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.report import (
    SwarmStageReportService,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver_contracts import (
    StageExecutionService,
    SwarmRuntimeResolver,
    SwarmRuntimeConfiguration,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmMethodPolicy,
    SwarmWorkflowPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    actor_has_role,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    resolve_event_contract,
)
from agentclaw.community.core.task.context_collab.l6_products.profile_reader import (
    ProductProfileReader,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.records import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.swarm_planning import (
    SwarmPlanningService,
)


@dataclass(frozen=True)
class SwarmPlanningCapabilities:
    services: Mapping[str, SwarmPlanningService]


class ProductSwarmRuntimeResolver:
    @inject
    def __init__(
        self,
        repository: ContextCollabRepositoryProtocol[ContextStatusRecord],
        catalog: ProductProfileReader,
        capabilities: SwarmPlanningCapabilities,
    ):
        self.repository, self.catalog, self.capabilities = (
            repository,
            catalog,
            capabilities,
        )

    def resolve(self, collet_id: str) -> SwarmRuntimeConfiguration:
        record = self.repository.get_current_status(collet_id)
        if record is None or record.task is None or record.product_instance is None:
            raise ContextCollabValidationError(
                "Swarm requires installed product and root Work"
            )
        ref = record.product_instance.profile_ref
        definition = self.catalog.get(ref.profile_id, ref.version)
        policy = definition.composition.require_policy("Swarm")
        if isinstance(policy, SwarmWorkflowPolicy):
            coordinator = ActorContext(record.task.principal_ref, source="swarm_driver")
            return SwarmRuntimeConfiguration(
                WorkflowPlanningService(policy.workflow),
                coordinator,
                self._start_actor(record, definition)
                if record.task.status == "created"
                else coordinator,
                max(100, 2 * (len(policy.workflow.nodes) + 1) + 4),
            )
        if not isinstance(policy, SwarmMethodPolicy):
            raise ContextCollabValidationError("Swarm requires its own planning policy")
        service_ref = policy.plan_service_ref
        if service_ref not in self.capabilities.services:
            raise ContextCollabValidationError(
                "product must explicitly configure an installed Swarm plan capability"
            )
        coordinator = ActorContext(record.task.principal_ref, source="swarm_driver")
        return SwarmRuntimeConfiguration(
            self.capabilities.services[service_ref],
            coordinator,
            self._start_actor(record, definition)
            if record.task.status == "created"
            else coordinator,
        )

    def workflow_for(self, collet_id: str, binding_id: str):
        record = self.repository.get_current_status(collet_id)
        if record is None:
            raise ContextCollabValidationError("workflow Collet not found")
        if record.product_instance is None:
            return None
        ref = record.product_instance.profile_ref
        definition = self.catalog.get(ref.profile_id, ref.version)
        if binding_id not in product_binding_refs(collet_id, definition):
            # Nested/non-product Swarm retains its explicitly unmanaged behavior.
            return None
        policies = [
            d.policy
            for d in definition.composition.required_methods
            if d.profile.method_kind == "Swarm"
        ]
        if len(policies) > 1:
            raise ContextCollabValidationError("ambiguous product Swarm policy")
        return (
            policies[0].workflow
            if policies and isinstance(policies[0], SwarmWorkflowPolicy)
            else None
        )

    @staticmethod
    def _start_actor(
        record: ContextStatusRecord, definition: ProductProfileDefinition
    ) -> ActorContext:
        """Resolve trusted startup provenance; L4 still adjudicates G1/G4/CAS.

        Prefer the accountable principal when the pinned contract permits it.
        Otherwise require one role holder, never guess among multiple executors.
        L5 must not interpret product contracts or fabricate role bindings.
        """
        task = record.task
        contract = resolve_event_contract(definition)
        action = next(
            (a for a in contract.actions if a.event_kind == "task.started"), None
        )
        # An undeclared start adds no role restriction here; G4 still rejects
        # it when the pinned contract requires an explicit control action.
        role = action.required_role if action is not None else None
        if role is None or actor_has_role(
            task, task.principal_ref, role, definition, record.events
        ):
            return ActorContext(task.principal_ref, source="swarm_driver")
        facts = current_participation(record.events)
        candidates = {
            p.principal_ref
            if p.principal_ref is not None
            else PrincipalRef(p.agent_ref.agent_type, p.agent_ref.agent_id)
            for p in facts.participants
        }
        if not facts.participants:
            # Match G4's explicit compatibility for pre-participation history.
            candidates.update(
                PrincipalRef(ref.agent_type, ref.agent_id) for ref in task.executor_refs
            )
        authorized = {
            actor
            for actor in candidates
            if actor_has_role(task, actor, role, definition, record.events)
        }
        if len(authorized) != 1:
            raise ContextCollabValidationError(
                "Swarm requires exactly one Task startup role holder"
            )
        return ActorContext(authorized.pop(), source="swarm_driver")


class SwarmRuntimeModule(Module):
    def configure(self, binder: Binder) -> None:
        from agentclaw.community.di.modules.task.swarm_execution import (
            SwarmExecutionModule,
        )

        binder.install(SwarmExecutionModule())
        binder.bind(
            StageExecutionService, to=UnavailableStageExecutionService, scope=singleton
        )
        binder.bind(
            SwarmArtifactEvidenceValidator,
            to=UnavailableSwarmArtifactValidator,
            scope=singleton,
        )
        binder.bind(
            SwarmWorkflowPolicyReader, to=ProductSwarmRuntimeResolver, scope=singleton
        )
        binder.bind(ProductProfilePublicationAuthorizer, to=ProfilePublisherAllowlist())
        binder.bind(
            ProductProfilePublicationServiceProtocol,
            to=ProductProfilePublicationService,
            scope=singleton,
        )
        binder.bind(SwarmPlanningCapabilities, to=SwarmPlanningCapabilities({}))
        binder.bind(
            SwarmRuntimeResolver, to=ProductSwarmRuntimeResolver, scope=singleton
        )
        binder.bind(
            SwarmStageReportService, to=SwarmStageReportService, scope=singleton
        )

    @provider
    def event_writer(
        self, repository: ContextCollabRepositoryProtocol[ContextStatusRecord]
    ) -> EventWriter:
        return repository

    @provider
    def stage_reports(self, service: SwarmStageReportService) -> StageReportProtocol:
        return service
