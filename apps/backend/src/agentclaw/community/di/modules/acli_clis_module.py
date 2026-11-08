"""Bindings for managing a bot's acli market CLIs."""
from __future__ import annotations

from typing import Annotated

from injector import Injector, Module, inject, provider, singleton

from agentclaw.community.api.acli_cli_service import AcliCliServiceProtocol
from agentclaw.community.core.acli_clis.arca_port import ArcaAcliPort
from agentclaw.community.core.acli_clis.market import AcliMarketSearch
from agentclaw.community.core.acli_clis.repository.store import (
    AcliOperationStore,
    SqlAcliOperationStore,
)
from agentclaw.community.core.acli_clis.service import AcliCliService
from agentclaw.community.core.acli_clis.tasks import (
    AcliCancelTaskHandler,
    AcliOperationTaskHandler,
    AcliTaskLifecycle,
)
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.mcp.services.cli_capabilities import (
    CliCapabilityManifestResolver,
)
from agentclaw.community.core.mcp.services.cli_passport_scope import (
    CliPassportScopeReconciler,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLockRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.repository.protocols.identity import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.platform import TaskQueueRepositoryProtocol
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules.config_module import _block
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from agentclaw.community.plugin_api.http_client import QUALIFIER_GENERAL, HttpClient
from agentclaw.community.plugin_api.passport import PassportPlugin


class _StageContexts:
    """``RuntimeContextPort``: one bot stage's device context, by the shared stage rule."""

    def __init__(
        self,
        *,
        injector: Injector,
        publish_repo: BotPublishRepositoryProtocol,
        binding_repo: DeviceBindingRepository,
        bot_repo: BotRepository,
    ) -> None:
        self._injector = injector
        self._publish_repo = publish_repo
        self._binding_repo = binding_repo
        self._bot_repo = bot_repo

    def resolve(self, *, bot_id: str, owner_id: str, stage: str):
        # Imported here: both modules reach the DI container at import time
        # (see ``bot_config_manifest/cli_tools/arca_port.py``).
        from agentclaw.community.core.devices.services.device_context_resolver import (
            DeviceContextResolver,
        )
        from agentclaw.community.core.engine_runtime.stage import resolve_stage_device_context

        return resolve_stage_device_context(
            self._injector.get(DeviceContextResolver),
            self._publish_repo,
            self._binding_repo,
            self._bot_repo,
            bot_id=bot_id,
            owner_id=owner_id,
            stage=stage,
        )


class AcliClisModule(Module):
    """Compose ``AcliCliService``, its engine port and its task handlers."""

    @singleton
    @provider
    @inject
    def acli_task_lifecycle(
        self,
        registry: HandlerRegistry,
        operation_handler: AcliOperationTaskHandler,
        cancel_handler: AcliCancelTaskHandler,
    ) -> AcliTaskLifecycle:
        return AcliTaskLifecycle(
            registry=registry, operation_handler=operation_handler, cancel_handler=cancel_handler
        )

    @singleton
    @provider
    def acli_market_config(self) -> cfg.AcliMarketConfig:
        """The acli market's search URL — empty unless the deployment overlay sets
        ``user_config.acli_market.search_url`` (it differs per environment)."""
        search_url = _block("acli_market").get("search_url", "")
        return cfg.AcliMarketConfig(
            search_url=search_url.strip() if isinstance(search_url, str) else "",
        )

    @singleton
    @provider
    @inject
    def acli_operation_store(self, db: DatabasePlugin) -> AcliOperationStore:
        return SqlAcliOperationStore(db)

    @singleton
    @provider
    @inject
    def acli_port(
        self,
        transport: DeviceAdapterTransport,
        publish_repo: BotPublishRepositoryProtocol,
        binding_repo: DeviceBindingRepository,
        bot_repository: BotRepository,
        injector: Injector,
    ) -> ArcaAcliPort:
        return ArcaAcliPort(
            contexts=_StageContexts(
                injector=injector, publish_repo=publish_repo,
                binding_repo=binding_repo, bot_repo=bot_repository,
            ),
            transport=transport,
        )

    @singleton
    @provider
    def cli_scope_reconciler(
        self,
        passport_plugin: PassportPlugin,
        identity_repository: CallerIdentityRepositoryProtocol,
    ) -> CliPassportScopeReconciler:
        return CliPassportScopeReconciler(
            passport_plugin=passport_plugin,
            identity_repository=identity_repository,
            manifest_resolver=CliCapabilityManifestResolver(),
        )

    @singleton
    @provider
    @inject
    def acli_operation_handler(
        self,
        store: AcliOperationStore,
        port: ArcaAcliPort,
        bot_repository: BotRepository,
        reconciler: CliPassportScopeReconciler,
    ) -> AcliOperationTaskHandler:
        return AcliOperationTaskHandler(
            store=store, port=port, bots=bot_repository, reconciler=reconciler
        )

    @singleton
    @provider
    @inject
    def acli_cancel_handler(
        self,
        store: AcliOperationStore,
        port: ArcaAcliPort,
        tasks: TaskQueueRepositoryProtocol,
    ) -> AcliCancelTaskHandler:
        return AcliCancelTaskHandler(store=store, port=port, tasks=tasks)

    @singleton
    @provider
    @inject
    def acli_cli_service(
        self,
        bot_repository: BotRepository,
        lock_repository: BotCollabLockRepositoryProtocol,
        identity_repository: CallerIdentityRepositoryProtocol,
        reconciler: CliPassportScopeReconciler,
        port: ArcaAcliPort,
        market_config: cfg.AcliMarketConfig,
        http_client: Annotated[HttpClient, QUALIFIER_GENERAL],
        collaborators: CollaboratorServiceProtocol,
        task_queue: TaskQueueService,
        task_repository: TaskQueueRepositoryProtocol,
        store: AcliOperationStore,
    ) -> AcliCliServiceProtocol:
        return AcliCliService(
            bot_repository=bot_repository,
            lock_repository=lock_repository,
            identity_repository=identity_repository,
            scope_reconciler=reconciler,
            manifest_resolver=CliCapabilityManifestResolver(),
            port=port,
            # The market search needs no container: it calls the configured
            # market API with the general client (no base URL of its own).
            market=AcliMarketSearch(
                http_client=http_client, search_url=market_config.search_url
            ),
            collaborators=collaborators,
            task_queue=task_queue,
            task_repository=task_repository,
            store=store,
        )


__all__ = ["AcliClisModule"]
