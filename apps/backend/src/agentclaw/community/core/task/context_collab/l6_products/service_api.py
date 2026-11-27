"""L6 compatibility Service API for combined Collet/Method/product creation.

Direct primitive operations belong to the L4 Service API. This upper-layer
extension retains the existing single-executor creation contract for HTTP and
library callers; root service_api only reexports these exact types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)


@dataclass
class CollaborationCreateCommand:
    """L6 composition input for the existing HTTP creation flow.

    Not an L4 command: product/method selection belongs to L6 composition.

    创建不设幂等键(spec 作者裁决:调用方无需传入防重号)。
    """

    to_space: SpaceRef
    principal_ref: PrincipalRef
    product_kind: str | None
    workflow_kind: str | None
    method_kind: str
    executor_ref: AgentRef | None
    objective: str
    resource_refs: list[ResourceRef]
    product_profile_ref: VersionedProfileRef | None = None
    # Historical/new Todo runtime addressing; not a Work principal or resource grant.
    executor_owner_id: str | None = None


@runtime_checkable
class ContextCollabServiceProtocol(ContextServiceProtocol, Protocol):
    """L6 compatibility API: L4 operations plus explicit upper-layer composition.

    New direct L4 consumers import ContextServiceProtocol from
    l4_context_primitives.context_service_protocol. Only create_collaboration
    adds upper-layer inputs; root service_api reexports canonical L4
    commands/results without wrappers.
    """

    def create_collaboration(
        self, command: CollaborationCreateCommand, context: ActorContext
    ) -> Collet:
        """L6 composition with explicit Method and optional product selection.

        Preserves the existing HTTP composition flow, including its single
        executor contract and SQL/MethodStore transaction boundary. Not L4.
        """
        ...
