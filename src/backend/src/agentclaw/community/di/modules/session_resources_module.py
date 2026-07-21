"""DI bindings for session resources."""
from __future__ import annotations

from typing import Annotated

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.session_resource_service import (
    SessionResourceServiceProtocol,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.session_resources.baas_client import (
    SessionResourceBaasClient,
)
from agentclaw.community.core.session_resources.materialization import (
    SessionResourceMaterializeHandler,
    SessionResourceTaskLifecycle,
)
from agentclaw.community.core.session_resources.repository.protocol import (
    SessionResourceRepositoryProtocol,
)
from agentclaw.community.core.session_resources.service import SessionResourceService
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_BAAS,
    HttpClient,
)
from agentclaw.community.plugins.session_resource_repository import (
    SessionResourceRepository,
)


class SessionResourcesModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            SessionResourceRepositoryProtocol,
            to=SessionResourceRepository,
            scope=singleton,
        )
        binder.bind(
            SessionResourceMaterializeHandler,
            to=SessionResourceMaterializeHandler,
            scope=singleton,
        )
        binder.bind(
            SessionResourceTaskLifecycle,
            to=SessionResourceTaskLifecycle,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def baas_client(
        self,
        http_client: Annotated[HttpClient, QUALIFIER_BAAS],
    ) -> SessionResourceBaasClient:
        return SessionResourceBaasClient(http_client)

    @singleton
    @provider
    @inject
    def service(
        self,
        repository: SessionResourceRepositoryProtocol,
        baas_client: SessionResourceBaasClient,
        task_queue: TaskQueueService,
        resolver: DeviceContextResolver,
    ) -> SessionResourceService:
        return SessionResourceService(
            repository=repository,
            baas_client=baas_client,
            task_queue=task_queue,
            device_context_resolver=resolver,
        )

    @singleton
    @provider
    @inject
    def service_protocol(
        self,
        service: SessionResourceService,
    ) -> SessionResourceServiceProtocol:
        return service
