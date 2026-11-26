"""Composition root for the new product runtime; external capabilities deny by default."""

from injector import Binder, Module, provider, singleton
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProductProfilePublicationAuthorizer,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.product_runtime import (
    OwnerProductRuntimeAuthorization,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    ProductRuntimeAuthorization,
    RolloutExecutionPort,
    RolloutInteractionPort,
    StageSessionMessagesPort,
    RolloutRepository,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_progress_protocol import (
    RolloutProgressServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_engine import (
    RolloutEngine,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_worker import (
    RolloutIntentWorker,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_repository import (
    SqlProductRuntimeRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.ports import (
    ProductProfileRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.service import (
    ProductProfileService,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.capabilities import (
    UnavailableRolloutExecution,
    ConservativeRolloutInteraction,
    UnavailableStageMessages,
)


class ProductProfileRuntimeModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            ProductRuntimeAuthorization,
            to=OwnerProductRuntimeAuthorization(),
            scope=singleton,
        )
        binder.bind(
            RolloutExecutionPort, to=UnavailableRolloutExecution(), scope=singleton
        )
        binder.bind(
            RolloutInteractionPort, to=ConservativeRolloutInteraction(), scope=singleton
        )
        binder.bind(
            StageSessionMessagesPort, to=UnavailableStageMessages(), scope=singleton
        )

    @singleton
    @provider
    def repository(self, db: DatabasePlugin) -> SqlProductRuntimeRepository:
        return SqlProductRuntimeRepository(db)

    @singleton
    @provider
    def profile_repository(
        self, repository: SqlProductRuntimeRepository
    ) -> ProductProfileRepository:
        return repository

    @singleton
    @provider
    def rollout_repository(
        self, repository: SqlProductRuntimeRepository
    ) -> RolloutRepository:
        return repository

    @singleton
    @provider
    def service(
        self,
        repository: ProductProfileRepository,
        publisher: ProductProfilePublicationAuthorizer,
        authorization: ProductRuntimeAuthorization,
        execution: RolloutExecutionPort,
        interaction: RolloutInteractionPort,
        messages: StageSessionMessagesPort,
    ) -> ProductProfileServiceProtocol:
        return ProductProfileService(
            repository, publisher, authorization, execution, interaction, messages
        )

    @singleton
    @provider
    def engine(
        self,
        repository: RolloutRepository,
        execution: RolloutExecutionPort,
        authorization: ProductRuntimeAuthorization,
    ) -> RolloutEngine:
        return RolloutEngine(repository, execution, authorization)

    @singleton
    @provider
    def worker(
        self,
        repository: RolloutRepository,
        engine: RolloutEngine,
        execution: RolloutExecutionPort,
    ) -> RolloutIntentWorker:
        return RolloutIntentWorker(repository, engine, execution)

    @singleton
    @provider
    def progress(self, engine: RolloutEngine) -> RolloutProgressServiceProtocol:
        return engine
