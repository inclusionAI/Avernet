"""Internal, transport-free Profile publication Service API and audit values."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
)


@dataclass(frozen=True)
class PublishedProductProfile:
    definition: ProductProfileDefinition
    definition_digest: str
    published_by: PrincipalRef
    published_at: datetime


@dataclass(frozen=True)
class ProductProfilePublishCommand:
    definition: ProductProfileDefinition


@runtime_checkable
class ProductProfilePublicationServiceProtocol(Protocol):
    def publish(
        self, command: ProductProfilePublishCommand, actor: ActorContext
    ) -> PublishedProductProfile: ...
    def import_profiles(
        self, definitions: tuple[ProductProfileDefinition, ...], actor: ActorContext
    ) -> tuple[PublishedProductProfile, ...]: ...
