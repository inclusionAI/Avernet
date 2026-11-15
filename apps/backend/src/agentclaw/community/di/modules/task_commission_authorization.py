"""Install commission's admission prerequisite at the existing G1 seam.

Both generic L4 writes and product Work calls use this seam; a designated role
is not proof of Open admission. Existing products/v1 retain structural behavior.
"""

from injector import inject

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    product_definition,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_method_kind,
    creation_profile_ref,
    creation_product_kind,
    creation_workflow_kind,
)
from agentclaw.community.core.task.context_collab.l6_products.instance import (
    ProductInstance,
)
from agentclaw.community.core.task.context_collab.products.task_commission.publication import (
    BotSelfApplicationAdmission,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskMethodOpsPort,
)


class CommissionAdmissionAuthorizer(StructuralContextCollabAuthorizer):
    @inject
    def __init__(self, catalog: ProductCatalog, method_ops: TaskMethodOpsPort) -> None:
        self._catalog = catalog
        self._method_ops = method_ops

    def check_append(
        self,
        collet: Collet,
        task: Task | None,
        creation_facts: dict,
        product_instance: ProductInstance | None,
        command,
        actor: ActorContext,
    ) -> None:
        super().check_append(
            collet, task, creation_facts, product_instance, command, actor
        )
        profile = product_definition(
            product_kind=creation_product_kind(creation_facts),
            workflow_kind=creation_workflow_kind(creation_facts),
            method_kind=creation_method_kind(creation_facts),
            profile_ref=creation_profile_ref(creation_facts),
            catalog=self._catalog,
        )
        if (
            profile is not None
            and profile.publication is not None
            and isinstance(profile.publication.admission, BotSelfApplicationAdmission)
            and command.event_kind
            in {"task.started", "task.progress.updated", "task.submitted"}
        ):
            self._method_ops.require_admitted(collet.collet_id, actor)
