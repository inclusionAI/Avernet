"""Narrow Profile storage seam; execution consumes only the lower RolloutRepository."""

from typing import Protocol, runtime_checkable
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    RolloutRepository,
    RuntimeSnapshot,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
)


@runtime_checkable
class ProductProfileRepository(RolloutRepository, Protocol):
    def get_profile(self, profile_id: int) -> tuple[ProductProfile, PrincipalRef]: ...
    def create_profile(
        self,
        name: str,
        composition: MethodCompositionTemplate,
        stages: str,
        context: ActorContext,
        request_id: str,
    ) -> ProductProfile: ...
    def create_collet(
        self,
        profile: ProductProfile,
        content: str,
        context: ActorContext,
        request_id: str,
    ) -> RuntimeSnapshot: ...
