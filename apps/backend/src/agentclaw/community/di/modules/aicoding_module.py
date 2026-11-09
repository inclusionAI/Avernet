"""AicodingModule — production singletons for the aicoding module.

Replaces the lazy module-global ``_service`` in
``core/aicoding/dependencies/workspace.py`` with an injector
``@singleton`` binding. ``WorkspaceService`` has ``@inject`` on its
constructor and pulls ``BotService`` + ``DeviceService`` straight
from the injector — no module-specific construction logic, so a
``configure(binder)`` self-binding is enough.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable

from agentclaw.community.api.aicoding.architect_rebind_service import (
    ArchitectRebindServiceProtocol,
)
from agentclaw.community.api.common_config_service import CommonConfigServiceProtocol
from agentclaw.community.api.data_proxy_service import DataProxyServiceProtocol
from agentclaw.community.api.workitem_service import WorkItemServiceProtocol
from agentclaw.community.api.workspace_service import WorkspaceServiceProtocol
from agentclaw.community.core.aicoding.protocols import (
    AicodingBotResolutionServiceProtocol,
    AicodingHostedWorkspaceServiceProtocol,
)
from agentclaw.community.core.aicoding.services.architect_rebind_service import (
    ArchitectRebindService,
)
from agentclaw.community.core.aicoding.services.bot_resolution_service import (
    AicodingBotResolutionService,
)
from agentclaw.community.core.aicoding.services.data_proxy_service import (
    DataProxyService,
)
from agentclaw.community.core.aicoding.services.hosted_workspace_service import (
    AicodingHostedWorkspaceService,
)
from agentclaw.community.core.aicoding.services.workspace_hosting_workitem_service import (
    WorkspaceHostingWorkItemService,
)
from agentclaw.community.core.aicoding.services.workspace_service import (
    WorkspaceService,
)
from agentclaw.community.core.bot_management.engines.aicoding.recycle_release import (
    ArcaRecycleTransport,
)
from agentclaw.community.core.bot_management.engines.aicoding.recycle_runtime import (
    AicodingRecycleRuntime,
)
from agentclaw.community.core.bot_management.engines.aicoding.recycle_state import (
    RecycleUnavailable,
)
from agentclaw.community.core.bot_management.engines.aicoding.recycle_task import (
    AicodingRecycleHandler,
    AicodingRecycleLifecycle,
    AicodingRecycleOperations,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import (
    RestartBackupDependencies,
)
from agentclaw.community.core.bot_management.engines.recycle_contract import (
    RecycleServices,
)
from agentclaw.community.core.bot_management.engines.registry import (
    configure_restart_common_config,
)
from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_service import (
    WorkspaceHostingService,
)
from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
)
from agentclaw.community.core.bot_startup_script.protocols import (
    TeclawEngineTestProtocol,
)
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    BotRestartLockRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.recycle import (
    BotRecycleRepositoryProtocol,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.di.config import TaskQueueConfig, TaskQueueWorkerConfig
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.utils.env_utils import get_current_env
from injector import (
    Binder,
    CallError,
    Injector,
    Module,
    UnsatisfiedRequirement,
    inject,
    provider,
    singleton,
)


class AICodingModule(Module):
    """Production bindings for the ai coding module.

    NOTE: ``WorkflowCatalogService`` reads corp ``AntCodeConfig`` — its binding
    lives in the corp-only ``CorpAICodingModule`` (corp + test columns), not here,
    so this base module (shipped to community) references no corp config (B8 review).
    """

    def configure(self, binder: Binder) -> None:
        binder.bind(WorkspaceService, to=WorkspaceService, scope=singleton)
        binder.bind(
            AicodingBotResolutionService,
            to=AicodingBotResolutionService,
            scope=singleton,
        )
        binder.bind(
            AicodingBotResolutionServiceProtocol,
            to=AicodingBotResolutionService,
            scope=singleton,
        )
        binder.bind(DataProxyService, to=DataProxyService, scope=singleton)
        binder.bind(
            WorkspaceHostingWorkItemService,
            to=WorkspaceHostingWorkItemService,
            scope=singleton,
        )
        binder.bind(ArchitectRebindService, to=ArchitectRebindService, scope=singleton)
        # Resolve only when a coding restart reads its switch, after all module
        # overrides are installed. Capturing this injector avoids a service locator.
        injector = binder.injector
        configure_restart_common_config(
            lambda: injector.get(CommonConfigServiceProtocol)
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

    @singleton
    @provider
    @inject
    def _architect_rebind_service_protocol(
        self, svc: ArchitectRebindService
    ) -> ArchitectRebindServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _hosted_workspace_service(
        self,
        bot_repo: BotRepository,
        template_service: TemplateService,
        injector: Injector,
    ) -> AicodingHostedWorkspaceService:
        # 托管工作空间（workspace hosting）是 corp 才安装的依赖：社区列为构造期未提供，
        # 解析失败返回 None，由服务自身的 ``_require_workspace_hosting`` 守卫报错（B8）。
        try:
            workspace_hosting_service = injector.get(WorkspaceHostingService)
        except (UnsatisfiedRequirement, CallError):
            workspace_hosting_service = None
        return AicodingHostedWorkspaceService(
            bot_repo=bot_repo,
            template_service=template_service,
            workspace_hosting_service=workspace_hosting_service,
        )

    @singleton
    @provider
    def _hosted_workspace_service_protocol(
        self, svc: AicodingHostedWorkspaceService
    ) -> AicodingHostedWorkspaceServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def recycle_operations(
        self,
        store: BotRecycleRepositoryProtocol,
        bots: BotRepository,
        queue: TaskQueueService,
        locks: BotRestartLockRepositoryProtocol,
        dependencies: RestartBackupDependencies,
        config: TaskQueueConfig,
        worker: TaskQueueWorkerConfig,
        injector: Injector,
        engine_test: Callable[[], TeclawEngineTestProtocol],
    ) -> AicodingRecycleOperations:
        @contextmanager
        def guard(bot):
            env, entity = get_current_env(), bot.get("entity_id")
            if not entity:
                raise RecycleUnavailable("Bot lock identity unavailable")
            lock = locks.acquire(
                env, entity, bot["bot_id"], "recycle:" + bot["owner_id"]
            )
            if lock is None:
                # Admission/receipt verification only: the durable journal, not
                # this short TTL, protects a long-running operation.
                stale = locks.get_if_stale(env, entity, bot["bot_id"], 120)
                # Never reap a legacy release/restart lock: provider calls may
                # outlive 120s without a durable recycle journal protecting them.
                if stale is not None and stale.holder_user_id.startswith("recycle:"):
                    locks.release(env, entity, bot["bot_id"], stale.lock_token)
                    lock = locks.acquire(
                        env, entity, bot["bot_id"], "recycle:" + bot["owner_id"]
                    )
            if lock is None:
                from agentclaw.community.core.bot_management.services.bot_service import (
                    BotInvalidLifecycleStateError,
                )

                raise BotInvalidLifecycleStateError(
                    bot_id=bot["bot_id"], current_status="BUSY"
                )
            try:
                yield
            finally:
                locks.release(env, entity, bot["bot_id"], lock.lock_token)

        return AicodingRecycleOperations(
            store=store,
            queue=queue,
            guard=guard,
            app=config.app,
            worker_enabled=worker.enabled,
            is_teclaw=lambda engine: engine_test().is_teclaw(engine),
            runtime=AicodingRecycleRuntime(
                lambda: injector.get(DeviceService),
                bots,
                dependencies,
                lambda: injector.get(ArcaRecycleTransport),
            ),
        )

    @singleton
    @provider
    @inject
    def recycle_task_lifecycle(
        self,
        service: AicodingRecycleOperations,
        registry: HandlerRegistry,
        passport: PassportPlugin,
    ) -> AicodingRecycleLifecycle:
        return AicodingRecycleLifecycle(
            registry, AicodingRecycleHandler(service, passport)
        )

    @singleton
    @provider
    @inject
    def recycle_services(self, injector: Injector) -> RecycleServices:
        return RecycleServices(
            operations=lambda: injector.get(AicodingRecycleOperations)
        )

    @singleton
    @provider
    def arca_recycle_transport(self) -> ArcaRecycleTransport:
        return ArcaRecycleTransport()
