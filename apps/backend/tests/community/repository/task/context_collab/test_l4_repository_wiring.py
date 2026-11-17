"""Composition roots bind L4 storage, not a renamed product repository."""

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.collet_creation import (
    StructuralColletCreationAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    CollaborationReader,
    ColletCreateCommand,
    TaskCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    ContextCollabRepositoryProtocol as L4RepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.repository import (
    ContextCollabRepository as L4Repository,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.records import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol as ProductRepositoryProtocol,
)
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.plugin_api.database import DatabasePlugin
from injector import Injector, InstanceProvider


def test_actual_l4_service_creation_neither_resolves_nor_calls_product_repository(db):
    class NoCatalogAccess:
        def __getattr__(self, name):
            raise AssertionError(f"creation must not query catalog: {name}")

    def reject_product_resolution():
        raise AssertionError("L4 must not resolve product repository")

    def overrides(binder):
        binder.bind(DatabasePlugin, to=db)
        binder.bind(ProductCatalog, to=InstanceProvider(NoCatalogAccess()))
        binder.bind(
            ContextCollabAuthorizerProtocol, to=StructuralColletCreationAuthorizer()
        )
        binder.bind(ProductRepositoryProtocol, to=reject_product_resolution)

    injector = Injector(
        [TaskPersistenceModule(), TaskModule(), overrides], auto_bind=False
    )
    repository = injector.get(L4RepositoryProtocol[ContextStatusRecord])
    assert type(repository) is L4Repository
    assert repository is injector.get(L4RepositoryProtocol[ContextStatusRecord])
    assert repository is injector.get(CollaborationReader)
    service = injector.get(ContextServiceProtocol)
    assert service._repository is repository
    actor = ActorContext(PrincipalRef("human", "owner"), "api")
    created = service.create_collet(
        ColletCreateCommand(
            actor.actor,
        ),
        actor,
    )
    state = repository.get_current_status(created.collet_id)
    assert state.collet == created
    assert state.product_instance is None
    assert state.task is None and state.events == []
    task = service.create_task(
        created.collet_id, TaskCreateCommand(1, actor.actor, "Deliver work"), actor
    )
    state = repository.get_current_status(created.collet_id)
    assert state.task == task and task.objective == "Deliver work"
    assert state.collet.version == 2 and task.version == 1
