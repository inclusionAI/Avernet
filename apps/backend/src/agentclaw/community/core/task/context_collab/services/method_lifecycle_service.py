"""Compatibility application facade; real Method operations live in L5.

New composition roots inject the recovery port into L5 directly. This legacy
constructor retains product_catalog= for callers migrating from services/.
"""

from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id as top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.lifecycle import (
    MethodLifecycleService as LayerMethodLifecycleService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _now as _now,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _shape_of as _shape_of,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes as current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.services.method_bootstrap import (
    ProductMethodBindingRecovery,
)
from agentclaw.community.core.task.context_collab.services.method_bootstrap import (
    ensure_top_level_binding as ensure_top_level_binding,
)
from agentclaw.community.core.task.context_collab.services.method_bootstrap import (
    project_created_context as project_created_context,
)
from injector import inject


class MethodLifecycleService(LayerMethodLifecycleService):
    """Compatibility application facade assembling product-aware recovery."""

    @inject
    def __init__(
        self,
        store: MethodStoreProtocol,
        authorizer: MethodOpsAuthorizerProtocol,
        collab_repo: ContextCollabRepositoryProtocol,
        *,
        product_catalog: ProductCatalog,
    ) -> None:
        self._product_catalog = product_catalog
        super().__init__(
            store,
            authorizer,
            collab_repo,
            recovery=ProductMethodBindingRecovery(store, collab_repo, product_catalog),
        )
