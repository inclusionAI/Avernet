"""Composition binding for Local Skill package uploads."""

from __future__ import annotations

from injector import Binder, Injector, inject, provider, singleton

from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.skill_center.services.local_skill_package_runtime import (
    LocalSkillPackageRuntime,
)
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    LocalSkillSecurityObservationRepositoryProtocol,
)
from agentclaw.community.core.skill_center.factories import SkillServiceFactory
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_service import (
    LocalSkillUploadService,
)
from agentclaw.community.core.skill_center.local_skill_security_upload_hook import (
    LocalSkillSecurityUploadHook,
)
from agentclaw.community.core.skill_center.services.local_skill_security_upload_observer import (
    LocalSkillSecurityUploadObserver,
    NoopLocalSkillSecurityUploadHook,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.skills_pool.edit_guard import SkillsPoolEditGuard
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectStoragePlugin,
)
from agentclaw.community.plugin_api.local_skill_security_provider import (
    LocalSkillSecurityProviderProtocol,
)
from agentclaw.community.di.local_skill_security_config import (
    LocalSkillSecurityObservationConfig,
    require_observation_capabilities,
)
from agentclaw.community.utils.env_utils import get_current_env


class LocalSkillUploadBindings:
    """Keep package validation composition out of the aggregate Skill module."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            LocalSkillSecurityUploadHook,
            to=NoopLocalSkillSecurityUploadHook,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def local_skill_package_runtime(
        self,
        resolver: DeviceContextResolver,
        transport: DeviceAdapterTransport,
    ) -> LocalSkillPackageRuntime:
        return LocalSkillPackageRuntime(resolver, transport)

    @singleton
    @provider
    @inject
    def local_skill_security_upload_observer(
        self,
        repository: LocalSkillSecurityObservationRepositoryProtocol,
        object_storage: ObjectStoragePlugin,
        queue: TaskQueueService,
        security_provider: LocalSkillSecurityProviderProtocol,
        security_config: LocalSkillSecurityObservationConfig,
    ) -> LocalSkillSecurityUploadObserver:
        if security_config.enabled:
            require_observation_capabilities(security_provider)
        if not isinstance(object_storage, ImmutableObjectStorageCapability):
            raise ValueError("Local Skill security requires immutable object storage")
        return LocalSkillSecurityUploadObserver(
            repository=repository,
            objects=object_storage,
            queue=queue,
            env_provider=get_current_env,
        )

    @singleton
    @provider
    @inject
    def local_skill_upload_service(
        self,
        skill_repo: SkillRepository,
        bot_repo: BotRepository,
        collaborator_service: CollaboratorServiceProtocol,
        skill_service_factory: SkillServiceFactory,
        edit_guard: SkillsPoolEditGuard,
        injector: Injector,
        runtime_reconciler: BotRuntimeProjectorProtocol,
        package_runtime: LocalSkillPackageRuntime,
        security_config: LocalSkillSecurityObservationConfig,
    ) -> LocalSkillUploadServiceProtocol:
        return LocalSkillUploadService(
            skill_repo,
            bot_repo,
            collaborator_service,
            skill_service_factory,
            edit_guard,
            lambda: injector.get(DeviceContextResolver),
            runtime_reconciler,
            SkillPackageValidator(SkillParser()),
            package_runtime,
            lambda: injector.get(LocalSkillSecurityUploadHook),
            security_config.enabled,
        )
