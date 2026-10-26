"""Default closed knowledge boundary; deployments may replace the provider/sync."""

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.di.space_knowledge_capture_config import (
    SpaceKnowledgeCaptureConfig,
)
from agentclaw.community.di.modules.config_module import read_user_config
from agentclaw.community.core.repository.protocols.space_persistence_effects import (
    SpacePersistenceEffects,
)
from agentclaw.community.core.repository.implementations.spaces.persistence_effects import (
    NoKnowledgeSpacePersistenceEffects,
)
from agentclaw.community.core.repository.implementations.spaces.knowledge_effects import (
    KnowledgeSpacePersistenceEffects,
)
from agentclaw.community.core.space_knowledge.capture_guard import (
    SpaceKnowledgeCaptureGuard,
)
from agentclaw.community.core.space_knowledge.capture_lifecycle import (
    SpaceKnowledgeCaptureLifecycle,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

from agentclaw.community.core.repository.protocols.platform import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.spaces import (
    SpaceKnowledgeSyncRepositoryProtocol,
)
from agentclaw.community.core.repository.implementations.spaces.knowledge_sync import (
    SpaceKnowledgeSyncRepository,
)
from agentclaw.community.core.space_knowledge.membership_sync import (
    SpaceMembershipSynchronizer,
)
from agentclaw.community.core.space_knowledge.membership_tasks import (
    SpaceMembershipSyncLifecycle,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.di.config import TaskQueueConfig
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSink
from agentclaw.community.utils.env_utils import get_current_env

from agentclaw.community.api.space_knowledge_service import (
    SpaceKnowledgeServiceProtocol,
)
from agentclaw.community.core.space_knowledge.service import SpaceKnowledgeService
from agentclaw.community.plugin_api.space_knowledge import (
    SpaceKnowledgeProvider,
    SpaceKnowledgeSync,
)
from agentclaw.community.plugins.unavailable.space_knowledge import (
    DisabledSpaceKnowledgeProvider,
    DisabledSpaceKnowledgeSync,
    DisabledSpaceMembershipSink,
)


class SpaceKnowledgeModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            SpaceMembershipSink, to=DisabledSpaceMembershipSink, scope=singleton
        )
        binder.bind(
            SpaceKnowledgeSyncRepositoryProtocol,
            to=SpaceKnowledgeSyncRepository,
            scope=singleton,
        )
        binder.bind(
            SpaceKnowledgeProvider, to=DisabledSpaceKnowledgeProvider, scope=singleton
        )
        binder.bind(SpaceKnowledgeSync, to=DisabledSpaceKnowledgeSync, scope=singleton)
        binder.bind(
            SpaceKnowledgeServiceProtocol, to=SpaceKnowledgeService, scope=singleton
        )

    @singleton
    @provider
    def capture_config(self) -> SpaceKnowledgeCaptureConfig:
        raw = read_user_config().get("space_knowledge_capture", {})
        if not isinstance(raw, dict) or set(raw) - {"enabled"}:
            raise ValueError(
                "space_knowledge_capture must be a mapping with only enabled"
            )
        enabled = raw.get("enabled", False)
        if type(enabled) is not bool:
            raise ValueError("space_knowledge_capture.enabled must be a boolean")
        return SpaceKnowledgeCaptureConfig(enabled=enabled)

    @singleton
    @provider
    @inject
    def capture_lifecycle(
        self,
        database: DatabasePlugin,
        config: SpaceKnowledgeCaptureConfig,
        sink: SpaceMembershipSink,
    ) -> SpaceKnowledgeCaptureLifecycle:
        effects = (
            KnowledgeSpacePersistenceEffects()
            if config.enabled
            else NoKnowledgeSpacePersistenceEffects()
        )
        return SpaceKnowledgeCaptureLifecycle(
            database,
            effects,
            sink,
            enabled=config.enabled,
            environment=get_current_env(),
        )

    @singleton
    @provider
    @inject
    def persistence_effects(
        self, capture: SpaceKnowledgeCaptureLifecycle
    ) -> SpacePersistenceEffects:
        return capture

    @singleton
    @provider
    @inject
    def capture_guard(
        self, capture: SpaceKnowledgeCaptureLifecycle
    ) -> SpaceKnowledgeCaptureGuard:
        return capture

    @singleton
    @provider
    @inject
    def membership_synchronizer(
        self,
        repository: SpaceKnowledgeSyncRepositoryProtocol,
        sink: SpaceMembershipSink,
        config: TaskQueueConfig,
        queue: TaskQueueRepositoryProtocol,
    ) -> SpaceMembershipSynchronizer:
        return SpaceMembershipSynchronizer(repository, sink, config.app, queue)

    @singleton
    @provider
    @inject
    def membership_lifecycle(
        self, registry: HandlerRegistry, sync: SpaceMembershipSynchronizer
    ) -> SpaceMembershipSyncLifecycle:
        return SpaceMembershipSyncLifecycle(registry, sync, get_current_env())
