"""AicodingModule — production singletons for the aicoding module.

Replaces the lazy module-global ``_service`` in
``core/aicoding/dependencies/workspace.py`` with an injector
``@singleton`` binding. ``WorkspaceService`` has ``@inject`` on its
constructor and pulls ``BotService`` + ``DeviceService`` straight
from the injector — no module-specific construction logic, so a
``configure(binder)`` self-binding is enough.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.data_proxy_service import DataProxyServiceProtocol
from agentclaw.community.api.workitem_service import WorkItemServiceProtocol
from agentclaw.community.api.workspace_service import WorkspaceServiceProtocol
from agentclaw.community.core.aicoding.services.data_proxy_service import DataProxyService
from agentclaw.community.core.aicoding.services.workspace_hosting_workitem_service import (
    WorkspaceHostingWorkItemService,
)
from agentclaw.community.core.aicoding.services.workspace_service import WorkspaceService


class AICodingModule(Module):
    """Production bindings for the ai coding module.

    NOTE: ``WorkflowCatalogService`` reads corp ``AntCodeConfig`` — its binding
    lives in the corp-only ``CorpAICodingModule`` (corp + test columns), not here,
    so this base module (shipped to community) references no corp config (B8 review).
    """

    def configure(self, binder: Binder) -> None:
        binder.bind(WorkspaceService, to=WorkspaceService, scope=singleton)
        binder.bind(DataProxyService, to=DataProxyService, scope=singleton)
        binder.bind(
            WorkspaceHostingWorkItemService,
            to=WorkspaceHostingWorkItemService,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def _workspace_service_protocol(
        self, svc: WorkspaceService
    ) -> WorkspaceServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _data_proxy_service_protocol(
        self, svc: DataProxyService
    ) -> DataProxyServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _workitem_service_protocol(
        self, svc: WorkspaceHostingWorkItemService
    ) -> WorkItemServiceProtocol:
        return svc
