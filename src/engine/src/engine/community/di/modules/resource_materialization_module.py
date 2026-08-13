"""DI bindings for shared resource materialization."""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from engine.community.config import load_temporary_url_settings
from engine.community.core.resource_materialization.service import (
    ResourceMaterializationService,
)
from engine.community.core.session_files.export_service import SessionFileExportService
from engine.community.core.session_files.service import SessionFileService
from engine.community.plugin_api.resource_materialization import (
    BaasMaterializationClient,
    BackendMaterializationCallbackClient,
    TemporaryUrlPullClient,
)
from engine.community.plugin_api.session_file_export import BaasSessionFileClient
from engine.community.plugins.resource_materialization import (
    HttpTemporaryUrlPullClient,
    NotConfiguredBaasMaterializationClient,
    NotConfiguredBackendMaterializationCallbackClient,
)
from engine.community.plugins.session_file_export import (
    NotConfiguredBaasSessionFileClient,
)


class ResourceMaterializationModule(Module):
    """Bind fail-closed transports; deploy profiles may override them."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            BaasMaterializationClient,
            to=NotConfiguredBaasMaterializationClient,
            scope=singleton,
        )
        settings = load_temporary_url_settings()
        temporary_client: TemporaryUrlPullClient = HttpTemporaryUrlPullClient(
            max_bytes=settings.max_bytes,
            timeout_seconds=settings.timeout_seconds,
        )
        binder.bind(
            TemporaryUrlPullClient,
            to=temporary_client,
            scope=singleton,
        )
        binder.bind(
            BaasSessionFileClient,
            to=NotConfiguredBaasSessionFileClient,
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
        temporary_url_pull_client: TemporaryUrlPullClient,
    ) -> ResourceMaterializationService:
        return ResourceMaterializationService(
            pull_client=pull_client,
            callback_client=callback_client,
            temporary_url_pull_client=temporary_url_pull_client,
        )

    @singleton
    @provider
    def session_file_service(self) -> SessionFileService:
        return SessionFileService()

    @singleton
    @provider
    @inject
    def session_file_export_service(
        self,
        session_file_service: SessionFileService,
        export_client: BaasSessionFileClient,
    ) -> SessionFileExportService:
        return SessionFileExportService(
            session_file_service=session_file_service,
            export_client=export_client,
        )
