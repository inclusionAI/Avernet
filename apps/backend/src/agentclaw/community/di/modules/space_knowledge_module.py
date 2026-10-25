"""Default closed knowledge boundary; deployments may replace the provider/sync."""

from injector import Binder, Module, inject, provider, singleton

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
    @inject
    def membership_synchronizer(
        self,
        repository: SpaceKnowledgeSyncRepositoryProtocol,
        sink: SpaceMembershipSink,
        config: TaskQueueConfig,
    ) -> SpaceMembershipSynchronizer:
        return SpaceMembershipSynchronizer(repository, sink, config.app)

    @singleton
    @provider
    @inject
    def membership_lifecycle(
        self, registry: HandlerRegistry, sync: SpaceMembershipSynchronizer
    ) -> SpaceMembershipSyncLifecycle:
        return SpaceMembershipSyncLifecycle(registry, sync, get_current_env())
