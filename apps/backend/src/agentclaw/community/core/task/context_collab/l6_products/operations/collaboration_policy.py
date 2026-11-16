"""L6 adapter: product-aware G1/G4 policy for the L4-owned service.

Only this adapter interprets the rich SQL read record and L6 selection. It
cannot start Work, append an event, query/project a service response or commit.
No storage lookup is repeated: policy and L4 consume the same read snapshot.
"""

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.product_contract import (
    validate_product_event,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.service_ports import (
    CollaborationPolicy,
    WorkLifecycle,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    product_definition,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_method_kind,
    creation_product_kind,
    creation_profile_ref,
    creation_workflow_kind,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    resolve_event_contract,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextStatusRecord,
)


class ProductCollaborationPolicy(CollaborationPolicy[ContextStatusRecord]):
    def __init__(
        self, catalog: ProductCatalog, authorizer: ContextCollabAuthorizerProtocol
    ) -> None:
        self._catalog = catalog
        self._authorizer = authorizer

    def check_append(
        self,
        state: ContextStatusRecord,
        command: EventAppendCommand,
        context: ActorContext,
    ) -> None:
        self._authorizer.check_append(
            state.collet,
            state.task,
            state.creation_facts,
            state.product_instance,
            command,
            context,
        )

    def validate_append(
        self,
        state: ContextStatusRecord,
        command: EventAppendCommand,
        context: ActorContext,
    ) -> WorkLifecycle:
        validate_product_event(
            catalog=self._catalog,
            creation_facts=state.creation_facts,
            product_instance=state.product_instance,
            task=state.task,
            event_kind=command.event_kind,
            primitive_type=command.primitive_type,
            visibility=command.visibility,
            payload=command.payload,
            actor=context.actor,
            parent_event=command.parent_event,
            events=state.events,
        )
        profile = product_definition(
            product_kind=creation_product_kind(state.creation_facts),
            workflow_kind=creation_workflow_kind(state.creation_facts),
            method_kind=creation_method_kind(state.creation_facts),
            profile_ref=creation_profile_ref(state.creation_facts),
            catalog=self._catalog,
        )
        machine_ref = (
            resolve_event_contract(profile).state_machine_ref
            if profile is not None
            else None
        )
        return WorkLifecycle(
            workflow_kind=machine_ref.profile_id
            if machine_ref
            else creation_workflow_kind(state.creation_facts),
            workflow_version=machine_ref.version if machine_ref else None,
        )

    def filter_visible_events(
        self, state: ContextStatusRecord, context: ActorContext
    ) -> list[EventRecord]:
        return self._authorizer.filter_visible_events(
            state.collet,
            state.task,
            state.creation_facts,
            state.product_instance,
            state.events,
            context,
        )
