"""The public five-method service must write the official five-table schema."""

from types import SimpleNamespace

import pytest
from injector import Injector
from sqlalchemy import MetaData, inspect

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
    actor_context_scope,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.product_runtime import (
    OwnerProductRuntimeAuthorization,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_controller import (
    MultiRolloutController,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
    StrategyDescriptor,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    RolloutRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.product_business_repository_adapter import (
    ProductBusinessRepositoryAdapter,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_models import (
    ColletRow,
    ColletRolloutRow,
    EventRow,
    ProductProfileRow,
    StageRolloutRow,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    default_composition,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.capabilities import (
    UnavailableRolloutExecution,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.service import (
    ProductProfileService,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.di.modules.task.product_profile_runtime import (
    ProductProfileRuntimeModule,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

pytestmark = pytest.mark.asyncio


class Execution:
    require = lambda self, *args: None  # noqa: E731


def capabilities():
    strategy = SimpleNamespace(
        require=lambda policy, stages, context: StrategyDescriptor(
            policy.strategy.adaptation_ref,
            "frozen acceptance",
            "trusted-evaluator@1",
            0,
            100,
        )
    )
    isolation = SimpleNamespace(require=lambda *args: 1)
    notifications = SimpleNamespace(require=lambda: None)
    return strategy, isolation, notifications


def install(adapter, service):
    strategy, isolation, notifications = capabilities()
    controller = MultiRolloutController(
        adapter,
        strategy,
        isolation,
        notifications,
        OwnerProductRuntimeAuthorization(),
        SimpleNamespace(propose=None),
    )
    service._multi_rollout = controller


async def test_di_create_profile_and_read_use_only_five_tables(business):
    h = business
    injector = Injector([ProductProfileRuntimeModule()], auto_bind=False)
    injector.binder.bind(DatabasePlugin, to=h.db)
    repository = injector.get(RolloutRepository)
    assert isinstance(repository, ProductBusinessRepositoryAdapter)
    service = injector.get(ProductProfileServiceProtocol)

    with actor_context_scope(h.owner):
        profile = await service.create_product_profile("coding", h.stages)
        stored, owner = repository.get_profile(profile.id)
    outsider = ActorContext(PrincipalRef("human", "outsider"), "api")
    with actor_context_scope(outsider):
        second_profile = await service.create_product_profile("coding", h.stages)
    assert second_profile.id != profile.id
    assert stored == profile
    assert stored.composition == default_composition()
    assert owner == h.owner.actor
    with h.db.orm_session() as session:
        assert session.query(ProductProfileRow).count() == 2
        assert session.get(ProductProfileRow, profile.id).status == "DRAFT"
    with pytest.raises(ContextCollabValidationError), actor_context_scope(h.owner):
        await service.create_product_collet(profile.id, "build a feature")
    assert set(inspect(h.engine).get_table_names()) == {
        table.name
        for table in (
            ProductProfileRow.__table__,
            ColletRow.__table__,
            ColletRolloutRow.__table__,
            StageRolloutRow.__table__,
            EventRow.__table__,
        )
    }
    with h.db.orm_session() as session:
        assert session.query(ColletRow).count() == 0
    assert MetaData().tables == {}


async def test_online_profile_successful_collet_is_written_to_business_graph(business):
    h = business
    adapter = ProductBusinessRepositoryAdapter(h.repo)
    service = ProductProfileService(
        adapter,
        OwnerProductRuntimeAuthorization(),
        Execution(),
        SimpleNamespace(propose=None),
        SimpleNamespace(read=None),
    )
    install(adapter, service)
    with actor_context_scope(h.owner):
        profile = await service.create_product_profile("coding", h.stages)
    with h.db.orm_session() as session:
        session.query(ProductProfileRow).filter_by(id=profile.id).update(
            {"status": "ONLINE"}
        )
    with actor_context_scope(h.owner):
        collet = await service.create_product_collet(profile.id, "build a feature")
        rollouts = await service.query_collet_rollout(collet.id)
    assert rollouts.revision == 1
    assert rollouts.current_rollout_id == rollouts[0].rollout_id
    assert [stage.name for stage in rollouts[0].stages] == ["z_design", "a_build"]
    assert all(stage.session_id == "" for stage in rollouts[0].stages)
    with h.db.orm_session() as session:
        assert session.query(ColletRow).count() == 1
        assert session.query(ColletRolloutRow).count() == 1
        assert session.query(StageRolloutRow).count() == 2
        assert session.query(EventRow).count() == 1


async def test_profile_creation_does_not_require_runtime_capabilities(business):
    h = business
    adapter = ProductBusinessRepositoryAdapter(h.repo)
    service = ProductProfileService(
        adapter,
        OwnerProductRuntimeAuthorization(),
        UnavailableRolloutExecution(),
        SimpleNamespace(propose=None),
        SimpleNamespace(read=None),
        None,
    )
    with actor_context_scope(h.owner):
        profile = await service.create_product_profile("coding", h.stages)
    assert profile.id > 0
