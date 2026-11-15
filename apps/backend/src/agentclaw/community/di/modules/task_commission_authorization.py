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
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    product_definition,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
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
        self, collet: Collet, spec: ContextSpec, command, actor: ActorContext
    ) -> None:
        super().check_append(collet, spec, command, actor)
        profile = product_definition(spec, self._catalog)
        if (
            profile is not None
            and profile.publication is not None
            and isinstance(profile.publication.admission, BotSelfApplicationAdmission)
            and command.event_kind
            in {"task.started", "task.progress.updated", "task.submitted"}
        ):
            self._method_ops.require_admitted(collet.collet_id, actor)
