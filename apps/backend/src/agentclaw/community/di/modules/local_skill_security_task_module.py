"""Composition of durable Local Skill security observation workers."""

from __future__ import annotations

from injector import Binder, inject, provider, singleton

from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    LocalSkillSecurityObservationRepositoryProtocol,
)
from agentclaw.community.plugin_api.local_skill_security_provider import (
    LocalSkillSecurityProviderProtocol,
)
from agentclaw.community.plugins.local.local_skill_security_provider import (
    UnavailableLocalSkillSecurityProvider,
)
from agentclaw.community.core.skill_center.services.local_skill_security_tasks import (
    LocalSkillSecurityScanTaskHandler,
    LocalSkillSecuritySyncTaskHandler,
    LocalSkillSecurityTaskLifecycle,
)
from agentclaw.community.core.skill_center.services.local_skill_security_reconciler import (
    LocalSkillSecurityTaskReconciler,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.di.config import TaskQueueWorkerConfig
from agentclaw.community.di.local_skill_security_config import (
    LocalSkillSecurityObservationConfig,
    require_observation_capabilities,
)
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectStoragePlugin,
)
from agentclaw.community.utils.env_utils import get_current_env


class LocalSkillSecurityTaskBindings:
    def configure(self, binder: Binder) -> None:
        binder.bind(
            LocalSkillSecurityProviderProtocol,
            to=UnavailableLocalSkillSecurityProvider,
            scope=singleton,
        )

    @singleton
    @provider
    def local_skill_security_observation_config(
        self,
    ) -> LocalSkillSecurityObservationConfig:
        return LocalSkillSecurityObservationConfig()

    @singleton
    @provider
    @inject
    def local_skill_security_scan_task_handler(
        self,
        repository: LocalSkillSecurityObservationRepositoryProtocol,
        object_storage: ObjectStoragePlugin,
        security_provider: LocalSkillSecurityProviderProtocol,
        security_config: LocalSkillSecurityObservationConfig,
    ) -> LocalSkillSecurityScanTaskHandler:
        snapshots = (
            object_storage
            if isinstance(object_storage, ImmutableObjectStorageCapability)
            else None
        )
        if security_config.enabled and snapshots is None:
            raise ValueError(
                "Local Skill security worker requires immutable snapshot storage"
            )
        return LocalSkillSecurityScanTaskHandler(
            repository=repository,
            snapshots=snapshots,
            provider=security_provider,
            env_provider=get_current_env,
            enabled=security_config.enabled,
        )

    @singleton
    @provider
    @inject
    def local_skill_security_sync_task_handler(
        self,
        repository: LocalSkillSecurityObservationRepositoryProtocol,
        security_provider: LocalSkillSecurityProviderProtocol,
        security_config: LocalSkillSecurityObservationConfig,
    ) -> LocalSkillSecuritySyncTaskHandler:
        return LocalSkillSecuritySyncTaskHandler(
            repository=repository,
            provider=security_provider,
            env_provider=get_current_env,
            enabled=security_config.enabled,
        )

    @singleton
    @provider
    @inject
    def local_skill_security_task_lifecycle(
        self,
        registry: HandlerRegistry,
        scan: LocalSkillSecurityScanTaskHandler,
        sync: LocalSkillSecuritySyncTaskHandler,
    ) -> LocalSkillSecurityTaskLifecycle:
        return LocalSkillSecurityTaskLifecycle(registry=registry, scan=scan, sync=sync)

    @singleton
    @provider
    @inject
    def local_skill_security_task_reconciler(
        self,
        repository: LocalSkillSecurityObservationRepositoryProtocol,
        object_storage: ObjectStoragePlugin,
        queue: TaskQueueService,
        config: TaskQueueWorkerConfig,
        security_config: LocalSkillSecurityObservationConfig,
        security_provider: LocalSkillSecurityProviderProtocol,
    ) -> LocalSkillSecurityTaskReconciler:
        if security_config.enabled:
            require_observation_capabilities(security_provider)
            if not isinstance(object_storage, ImmutableObjectStorageCapability):
                raise ValueError(
                    "Local Skill security recovery requires immutable snapshots"
                )
        return LocalSkillSecurityTaskReconciler(
            repository=repository,
            snapshots=object_storage,
            queue=queue,
            env_provider=get_current_env,
            worker_enabled=config.enabled and security_config.enabled,
        )


__all__ = ["LocalSkillSecurityTaskBindings", "UnavailableLocalSkillSecurityProvider"]
