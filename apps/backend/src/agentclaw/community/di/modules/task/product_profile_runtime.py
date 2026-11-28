"""Composition root for the product runtime and hosted capability ports.

Every profile uses the same product runtime assembly. Infrastructure modules
decide whether ``OpenApiBotPort``/``BotRepository``/the notification sender are
installed; when one required port is absent this module keeps the corresponding
fail-closed default rather than simulating runtime operation.
"""

from injector import Module, inject, provider, singleton
from injector import Injector

from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.core.task.task_discovery.notify_messages_provider import (
    NotifyMessagesProvider,
)
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
from agentclaw.community.core.task.context_collab.l5_methods.hosted_rollout_capabilities import (
    AdaptiveStageImprovementStrategy,
    BotRepositoryRolloutIdentity,
    ChannelFeedbackNotifications,
    FeedbackShutdownInteraction,
    OpenApiCandidateIsolation,
    OwnedBotProductRuntimeAuthorization,
    SignedOpenApiEvidence,
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
from agentclaw.community.core.task.context_collab.l5_methods.openapi_rollout_execution import (
    OpenApiRolloutExecution,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.session_messages import (
    OpenApiStageSessionMessages,
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
    UnavailableStageMessages,
)


class ProductProfileRuntimeModule(Module):
    """Install the same product logic and resolve only installed host ports."""

    @staticmethod
    def _optional(injector: Injector, interface):
        try:
            return injector.get(interface)
        except Exception:  # noqa: BLE001 standalone injector has no work-row binding
            return None

    @singleton
    @provider
    @inject
    def authorization(self, injector: Injector) -> ProductRuntimeAuthorization:
        bots = self._optional(injector, BotRepository)
        if bots is None:
            return OwnerProductRuntimeAuthorization()
        return OwnedBotProductRuntimeAuthorization(BotRepositoryRolloutIdentity(bots))

    @singleton
    @provider
    @inject
    def execution(self, injector: Injector) -> RolloutExecutionPort:
        bot = self._optional(injector, OpenApiBotPort)
        bots = self._optional(injector, BotRepository)
        if bot is None or bots is None:
            return UnavailableRolloutExecution()
        return OpenApiRolloutExecution(
            bot,
            BotRepositoryRolloutIdentity(bots),
            SignedOpenApiEvidence(),
        )

    @singleton
    @provider
    @inject
    def strategy(self, injector: Injector) -> MultiRolloutStrategyPort:
        bot = self._optional(injector, OpenApiBotPort)
        bots = self._optional(injector, BotRepository)
        if bot is None or bots is None:
            return UnavailableMultiRolloutStrategy()
        identities = BotRepositoryRolloutIdentity(bots)
        return AdaptiveStageImprovementStrategy(bot, identities)

    @singleton
    @provider
    @inject
    def isolation(self, injector: Injector) -> MultiRolloutIsolationPort:
        bots = self._optional(injector, BotRepository)
        if bots is None:
            return UnavailableMultiRolloutIsolation()
        return OpenApiCandidateIsolation(BotRepositoryRolloutIdentity(bots))

    @singleton
    @provider
    @inject
    def notifications(self, injector: Injector) -> MultiRolloutNotificationPort:
        sender = self._optional(injector, NotifyMessagesProvider)
        if sender is None:
            return UnavailableMultiRolloutNotifications()
        return ChannelFeedbackNotifications(sender)

    @singleton
    @provider
    @inject
    def interaction(self) -> RolloutInteractionPort:
        return FeedbackShutdownInteraction()

    @singleton
    @provider
    @inject
    def messages(self, injector: Injector) -> StageSessionMessagesPort:
        bot = self._optional(injector, OpenApiBotPort)
        if bot is None:
            return UnavailableStageMessages()
        return OpenApiStageSessionMessages(bot)

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
