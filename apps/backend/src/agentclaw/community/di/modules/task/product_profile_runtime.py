"""Composition root for the new product runtime; external capabilities deny by default."""

from injector import Binder, Module, provider, singleton
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.task.context_collab.g1_secure_govern.product_runtime import (
    OwnerProductRuntimeAuthorization,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    AuthenticatedProfilePublication,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_use import (
    AuthenticatedProductProfileUse,
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
from agentclaw.community.core.task.context_collab.l6_products.persistence.product_business_repository_adapter import (
    ProductBusinessRepositoryAdapter,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_repository import (
    SqlProductBusinessRepository,
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


from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
    MultiRolloutStrategyPort,
    MultiRolloutIsolationPort,
    MultiRolloutNotificationPort,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_capabilities import (
    UnavailableMultiRolloutStrategy,
    UnavailableMultiRolloutIsolation,
    UnavailableMultiRolloutNotifications,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_controller import (
    MultiRolloutController,
)


class ProductProfileRuntimeModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            MultiRolloutStrategyPort,
            to=UnavailableMultiRolloutStrategy(),
            scope=singleton,
        )
        binder.bind(
            MultiRolloutIsolationPort,
            to=UnavailableMultiRolloutIsolation(),
            scope=singleton,
        )
        binder.bind(
            MultiRolloutNotificationPort,
            to=UnavailableMultiRolloutNotifications(),
            scope=singleton,
        )
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
    def business_repository(self, db: DatabasePlugin) -> SqlProductBusinessRepository:
        return SqlProductBusinessRepository(
            db,
            AuthenticatedProfilePublication(),
            AuthenticatedProductProfileUse(),
            OwnerProductRuntimeAuthorization(),
        )

    @singleton
    @provider
    def profile_repository(
        self, repository: SqlProductBusinessRepository
    ) -> ProductProfileRepository:
        return ProductBusinessRepositoryAdapter(repository)

    @singleton
    @provider
    def rollout_repository(
        self, repository: SqlProductBusinessRepository
    ) -> RolloutRepository:
        return ProductBusinessRepositoryAdapter(repository)

    @singleton
    @provider
    def multi_controller(
        self,
        repository: RolloutRepository,
        strategy: MultiRolloutStrategyPort,
        isolation: MultiRolloutIsolationPort,
        notifications: MultiRolloutNotificationPort,
        authorization: ProductRuntimeAuthorization,
        interaction: RolloutInteractionPort,
    ) -> MultiRolloutController:
        return MultiRolloutController(
            repository, strategy, isolation, notifications, authorization, interaction
        )

    @singleton
    @provider
    def service(
        self,
        repository: ProductProfileRepository,
        authorization: ProductRuntimeAuthorization,
        execution: RolloutExecutionPort,
        interaction: RolloutInteractionPort,
        messages: StageSessionMessagesPort,
        multi_rollout: MultiRolloutController,
    ) -> ProductProfileServiceProtocol:
        return ProductProfileService(
            repository,
            authorization,
            execution,
            interaction,
            messages,
            multi_rollout,
        )

    @singleton
    @provider
    def engine(
        self,
        repository: RolloutRepository,
        execution: RolloutExecutionPort,
        authorization: ProductRuntimeAuthorization,
        isolation: MultiRolloutIsolationPort,
    ) -> RolloutEngine:
        return RolloutEngine(repository, execution, authorization, isolation)

    @singleton
    @provider
    def worker(
        self,
        repository: RolloutRepository,
        engine: RolloutEngine,
        execution: RolloutExecutionPort,
        multi_rollout: MultiRolloutController,
    ) -> RolloutIntentWorker:
        return RolloutIntentWorker(repository, engine, execution, multi_rollout)

    @singleton
    @provider
    def progress(self, engine: RolloutEngine) -> RolloutProgressServiceProtocol:
        return engine
