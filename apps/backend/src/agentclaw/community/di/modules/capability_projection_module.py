"""Wire durable capability projection persistence and recovery."""

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.repository.implementations.skill_center.capability_projection import (
    CapabilityProjectionRepository,
)
from agentclaw.community.core.repository.protocols.capability_projection import (
    CapabilityProjectionRepositoryProtocol,
)
from agentclaw.community.core.skill_center.capability_projection_reconcile import (
    CapabilityProjectionReconcileProtocol,
)
from agentclaw.community.core.skill_center.services.capability_projection_recovery import (
    CapabilityProjectionRecovery,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.di.config import TaskQueueWorkerConfig


class CapabilityProjectionModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            CapabilityProjectionRepositoryProtocol,
            to=CapabilityProjectionRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def capability_projection_recovery(
        self, repository: CapabilityProjectionRepositoryProtocol,
        reconciler: CapabilityProjectionReconcileProtocol,
        registry: HandlerRegistry, queue: TaskQueueService,
        config: TaskQueueWorkerConfig,
    ) -> CapabilityProjectionRecovery:
        return CapabilityProjectionRecovery(
            repository=repository, reconciler=reconciler,
            registry=registry, queue=queue, worker_enabled=config.enabled,
        )
