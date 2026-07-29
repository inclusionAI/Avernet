"""DI bindings for shared resource materialization."""
from __future__ import annotations

from engine.community.core.resource_materialization.service import (
    ResourceMaterializationService,
)
from engine.community.core.session_files.service import SessionFileService
from engine.community.plugin_api.resource_materialization import (
    BaasMaterializationClient,
    BackendMaterializationCallbackClient,
)
from engine.community.plugins.resource_materialization import (
    NotConfiguredBaasMaterializationClient,
    NotConfiguredBackendMaterializationCallbackClient,
)
from injector import Binder, Module, inject, provider, singleton


class ResourceMaterializationModule(Module):
    """Bind fail-closed transports; deploy profiles may override them."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            BaasMaterializationClient,
            to=NotConfiguredBaasMaterializationClient,
            scope=singleton,
        )
        binder.bind(
            BackendMaterializationCallbackClient,
            to=NotConfiguredBackendMaterializationCallbackClient,
            scope=singleton,
        )
    @singleton
    @provider
    @inject
    def service(
        self,
        pull_client: BaasMaterializationClient,
        callback_client: BackendMaterializationCallbackClient,
    ) -> ResourceMaterializationService:
        return ResourceMaterializationService(
            pull_client=pull_client,
            callback_client=callback_client,
        )

    @singleton
    @provider
    def session_file_service(self) -> SessionFileService:
        return SessionFileService()
