"""Product policy with live Open enrollment checks on root execution writes."""

from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.service_ports import (
    WorkLifecycle,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.records import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.l6_products.profile_reader import (
    ProductProfileReader,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_policy import (
    ProductCollaborationPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.open_enrollment import (
    require_current_enrollment,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)


class OpenEnrollmentCollaborationPolicy(ProductCollaborationPolicy):
    def __init__(
        self,
        catalog: ProductProfileReader,
        authorizer: ContextCollabAuthorizerProtocol,
        store: MethodStoreProtocol,
    ) -> None:
        super().__init__(catalog, authorizer)
        self.store = store

    def validate_append(
        self,
        state: ContextStatusRecord,
        command: EventAppendCommand,
        context: ActorContext,
    ) -> WorkLifecycle:
        if command.event_kind in {
            "task.started",
            "task.progress.updated",
            "task.submitted",
        }:
            require_current_enrollment(state, self._catalog, self.store)
        return super().validate_append(state, command, context)
