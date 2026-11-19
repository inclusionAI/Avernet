"""Product instantiation/enrollment and the product-aware L4 policy adapter."""

from injector import Binder, Module, provider, singleton

from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service import (
    ContextService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import ProductCatalog
from agentclaw.community.core.task.context_collab.l6_products.open_enrollment import (
    ProductOpenEnrollmentServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.open_enrollment import (
    ProductOpenEnrollmentService,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.open_enrollment_policy import (
    OpenEnrollmentCollaborationPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.product_instantiation import (
    ProductInstantiationService,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.records import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)


class ContextCollabRuntimeModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            ProductInstantiationServiceProtocol,
            to=ProductInstantiationService,
            scope=singleton,
        )
        binder.bind(
            ProductOpenEnrollmentServiceProtocol,
            to=ProductOpenEnrollmentService,
            scope=singleton,
        )

    @singleton
    @provider
    def l4_context_service(
        self,
        repository: ContextCollabRepositoryProtocol[ContextStatusRecord],
        authorizer: ContextCollabAuthorizerProtocol,
        catalog: ProductCatalog,
        store: MethodStoreProtocol,
    ) -> ContextServiceProtocol:
        """L4 owns operations; the adapter retains G1/G4 and live Open checks."""
        return ContextService(
            repository,
            OpenEnrollmentCollaborationPolicy(catalog, authorizer, store),
            authorizer,
        )
