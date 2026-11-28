"""Real Profile service/SQL seeds with an isolated outbound execution boundary."""

import asyncio
from uuid import uuid4
from agentclaw.community.api.task.product_profile_service import (
    ProductProfileServiceProtocol,
)
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
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmStagesPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
    MethodTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_repository import (
    SqlProductRuntimeRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.service import (
    ProductProfileService,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    default_composition,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.capabilities import (
    UnavailableRolloutExecution,
    ConservativeRolloutInteraction,
    UnavailableStageMessages,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_controller import (
    MultiRolloutController,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
    StrategyDescriptor,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_capabilities import (
    UnavailableMultiRolloutStrategy,
    UnavailableMultiRolloutIsolation,
    UnavailableMultiRolloutNotifications,
)


OWNER = ActorContext(PrincipalRef("human", "u1"), "api")
STAGES = """stages:
  - name: implementation
    candidates: [{type: bot, uuid: worker}]
    depends_on: []
    acceptance: {condition: tests pass}
"""
COMPOSITION = MethodCompositionTemplate(
    (
        MethodTemplate(
            get_profile("Swarm"),
            SwarmStagesPolicy(
                "endpoint-execution/v1", "endpoint-evidence/v1", "stages"
            ),
        ),
    )
)
PROFILE_BODY = {
    "name": "endpoint-profile",
    "stages": STAGES,
}


class IsolatedExecutionBoundary(UnavailableRolloutExecution):
    def require(self, execution_ref, acceptance_ref, stages):
        if (execution_ref, acceptance_ref) not in {
            ("endpoint-execution/v1", "endpoint-evidence/v1"),
            ("openapi-bot/v1", "trusted-evidence/v1"),
        }:
            raise ContextCollabValidationError("unknown endpoint-test capabilities")


class IsolatedStrategyBoundary(UnavailableMultiRolloutStrategy):
    def require(self, policy, stages, context):
        assert policy == default_composition().required_methods[0].policy
        return StrategyDescriptor(
            policy.strategy.adaptation_ref,
            "static acceptance",
            "isolated-evidence/v1",
            0,
            100,
        )


class IsolatedWorkspaceBoundary(UnavailableMultiRolloutIsolation):
    def require(self, reference, execution_ref, stages, context):
        assert reference == "per-candidate-workspace@1"
        return 1


class IsolatedNotificationBoundary(UnavailableMultiRolloutNotifications):
    def require(self):
        pass


def seed_service(world):
    repository = SqlProductRuntimeRepository(world.get(DatabasePlugin))
    authorization = OwnerProductRuntimeAuthorization()
    interaction = ConservativeRolloutInteraction()
    controller = MultiRolloutController(
        repository,
        IsolatedStrategyBoundary(),
        IsolatedWorkspaceBoundary(),
        IsolatedNotificationBoundary(),
        authorization,
        interaction,
    )
    service = ProductProfileService(
        repository,
        authorization,
        IsolatedExecutionBoundary(),
        interaction,
        UnavailableStageMessages(),
        controller,
    )
    world.injector.binder.bind(ProductProfileServiceProtocol, to=service)
    return service


def seed_profile(world):
    service = seed_service(world)
    # Existing stored profiles remain readable/runnable; only new creation defaults change.
    profile = SqlProductRuntimeRepository(world.get(DatabasePlugin)).create_profile(
        "seed-profile", COMPOSITION, STAGES, OWNER, uuid4().hex
    )
    assert profile.id == 1
    return service


def seed_collet(world):
    service = seed_profile(world)
    with actor_context_scope(OWNER):
        collet = asyncio.run(service.create_product_collet(1, "endpoint request"))
        values = asyncio.run(service.query_collet_rollout(1))
    assert collet.id == 1
    assert values[0].rollout_id == 2 and values[0].stages[0].stage_rollout_id == 3
    return service


def seed_intervention(world):
    service = seed_collet(world)
    with actor_context_scope(OWNER):
        asyncio.run(
            service.interactive(2, "endpoint-intervention", "original feedback")
        )
