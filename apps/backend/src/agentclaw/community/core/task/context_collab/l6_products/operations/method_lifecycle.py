"""L6 compatibility constructor for product-aware recovery; Method operations are L5.

New composition roots inject the recovery port into L5 directly. This legacy
constructor retains product_catalog= for callers migrating from services/.
"""

from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.lifecycle import (
    MethodLifecycleService as LayerMethodLifecycleService,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.method_bootstrap import (
    ProductMethodBindingRecovery,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from injector import inject


class MethodLifecycleService(LayerMethodLifecycleService):
    """L6 compatibility constructor assembling product-aware L5 recovery."""

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
