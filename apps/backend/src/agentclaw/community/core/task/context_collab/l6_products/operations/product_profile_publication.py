"""Explicit internal publication command; authoring remains a pure operation."""

from injector import inject

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProductProfilePublicationAuthorizer,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.profile_repository import (
    SqlProductProfileRepository,
)


from agentclaw.community.core.task.context_collab.l6_products.profile_publication import (
    PublishedProductProfile as PublishedProductProfile,
    ProductProfilePublishCommand as ProductProfilePublishCommand,
    ProductProfilePublicationServiceProtocol as ProductProfilePublicationServiceProtocol,
)


class ProductProfilePublicationService:
    @inject
    def __init__(
        self,
        repository: SqlProductProfileRepository,
        authorizer: ProductProfilePublicationAuthorizer,
    ) -> None:
        self.repository, self.authorizer = repository, authorizer

    def publish(
        self, command: ProductProfilePublishCommand, actor: ActorContext
    ) -> PublishedProductProfile:
        definition = command.definition
        self.authorizer.check_publish(
            definition.ref.profile_id, definition.ref.version, actor
        )
        return self.repository.publish_many((definition,), actor.actor)[0]

    def import_profiles(
        self, definitions: tuple[ProductProfileDefinition, ...], actor: ActorContext
    ) -> tuple[PublishedProductProfile, ...]:
        """Explicit transactionally replayable seed/import for the current scope."""
        for definition in definitions:
            self.authorizer.check_publish(
                definition.ref.profile_id, definition.ref.version, actor
            )
        return self.repository.publish_many(
            definitions, actor.actor, verify_history=True
        )
