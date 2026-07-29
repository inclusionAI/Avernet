"""Task concern — community binding (Phase 1-5).

Binds the task Protocols to community impls:
- ``TaskService`` → real :class:`TaskService` (intake/state/on_event/query +
  create-time panel popup), backed by the ORM repos (``ac_task`` /
  ``ac_task_event`` / ``ac_task_execution_graph``).
- ``TaskRepo`` / ``TaskEventRepo`` → unified ORM repos (SQLite local/CI via
  ``DatabasePlugin``; OceanBase prod — same body, different injected DB).
- ``PanelEventPublisher`` → :class:`EventBusPanelPublisher`.
- The 4 orchestration Ports (Discover/Driver/Execution) +
  ``BcsCollaborationProtocol`` remain Noop until their real impls land.

Corp Phase 6 overrides the infra-bound Protocols with real adapters.
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.api.task import (
    BbsExecutor,
    BcsCollaborationProtocol,
    BotDiscoverPort,
    DecomposerPort,
    ExecutionPort,
    PanelDeliveryPort,
    PanelEventPublisher,
    TaskDriverPort,
    TaskScheduler,
    TaskService,
)
from agentclaw.community.core.task.domain.repository import (
    TaskEventRepo,
    TaskRepo,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugins.community.task.panel_carrier import TaskPanelCarrier


class CommunityTaskModule(Module):
    """community: real TaskService + TaskScheduler + ORM repos; Ports still Noop (Phase 4/5)."""

    @singleton
    @provider
    def task_repo(self, db: DatabasePlugin) -> TaskRepo:
        # Phase 1.3: unified ORM repo (SQLite local/CI + OceanBase prod).
        from agentclaw.community.plugins.task_repository import OrmTaskRepository
        return OrmTaskRepository(db)

    @singleton
    @provider
    def event_repo(self, db: DatabasePlugin) -> TaskEventRepo:
        from agentclaw.community.plugins.task_event_repository import (
            OrmTaskEventRepository,
        )
        return OrmTaskEventRepository(db)

    @singleton
    @provider
    def panel_publisher(self) -> PanelEventPublisher:
        from agentclaw.community.plugins.community.task.panel_publisher import (
            EventBusPanelPublisher,
        )
        return EventBusPanelPublisher()

    @singleton
    @provider
    def panel_delivery_port(self) -> PanelDeliveryPort:
        # Phase 4.5.3: community default = Noop (no chat push bus; the frontend
        # create-flow calls openTaskPanel directly). Corp/transport-bridge
        # wires a real chat-WS <AixUI panel> push (TODO Phase 6).
        from agentclaw.community.plugins.community.task.panel_carrier import (
            NoopPanelDelivery,
        )
        return NoopPanelDelivery()

    @singleton
    @provider
    def task_panel_carrier(self, delivery: PanelDeliveryPort) -> TaskPanelCarrier:
        # Phase 4.5.3 carrier transport: subscribes TaskPanelEvent → delivery
        # port at lifespan startup. Implements Lifecycle so the composition
        # root auto-discovers + installs it.
        return TaskPanelCarrier(delivery)

    @singleton
    @provider
    def task_service(
        self,
        task_repo: TaskRepo,
        event_repo: TaskEventRepo,
        panel_publisher: PanelEventPublisher,
        bcs_collab: BcsCollaborationProtocol,
    ) -> TaskService:
        from agentclaw.community.core.task.services import TaskService as RealTaskService
        return RealTaskService(task_repo, event_repo, panel_publisher, bcs_collab)

    @singleton
    @provider
    def task_scheduler(
        self,
        task_service: TaskService,
        discover: BotDiscoverPort,
        driver: TaskDriverPort,
        decomposer: DecomposerPort,
    ) -> TaskScheduler:
        from agentclaw.community.core.task.services import TaskScheduler as RealScheduler
        return RealScheduler(task_service, discover, driver, decomposer)

    @singleton
    @provider
    def bbs_executor(self, task_service: TaskService) -> BbsExecutor:
        # Phase 5: shared blackboard = TaskExecutionGraph; reads via TaskService
        # query face, writes via on_event (no Scheduler tick).
        from agentclaw.community.core.task.services import BbsExecutorService
        return BbsExecutorService(task_service)

    @singleton
    @provider
    def bot_discover_port(self) -> BotDiscoverPort:
        from agentclaw.community.plugins.community.task import NoopBotDiscoverPort
        return NoopBotDiscoverPort()

    @singleton
    @provider
    def decomposer_port(self, task_repo: TaskRepo) -> DecomposerPort:
        # Phase 4.3: rule-based DecomposerService (LLM decompose stays in the
        # owner-bot SKILL; community never holds an LLM prompt).
        from agentclaw.community.core.task.services.decomposer_service import (
            DecomposerService,
        )
        return DecomposerService(task_repo)

    @singleton
    @provider
    def task_driver_port(self) -> TaskDriverPort:
        from agentclaw.community.plugins.community.task import NoopTaskDriverPort
        return NoopTaskDriverPort()

    @singleton
    @provider
    def execution_port(self) -> ExecutionPort:
        from agentclaw.community.plugins.community.task import NoopExecutionPort
        return NoopExecutionPort()

    @singleton
    @provider
    def bcs_collaboration_port(self) -> BcsCollaborationProtocol:
        # Phase 4 (plan §2.4): Noop fake-SM-graph; real httpx BCS impl bound via
        # BcsCollaborationHttpxModule when a local BCS base URL is configured.
        from agentclaw.community.plugins.community.task import NoopBcsCollaborationPort
        return NoopBcsCollaborationPort()