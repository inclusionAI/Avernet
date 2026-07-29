"""Wiring tests for the task DI module (Phase 0.8).

Asserts ``CommunityTaskModule`` binds all 6 task Protocols to their Noop impls
via an isolated ``Injector``. Phase 2+ will swap these for real impls; this
test guards the binding contract so a missing/wrong provider fails fast.
"""
from __future__ import annotations

from injector import Injector

from agentclaw.community.api.task import (
    BcsCollaborationProtocol,
    BotDiscoverPort,
    DecomposerPort,
    ExecutionPort,
    TaskDriverPort,
    TaskService,
)
from agentclaw.community.di.modules.infrastructure.community.task import (
    CommunityTaskModule,
)
from agentclaw.community.plugins.community.task import (
    NoopBcsCollaborationPort,
    NoopBotDiscoverPort,
    NoopDecomposerPort,
    NoopExecutionPort,
    NoopTaskDriverPort,
    NoopTaskService,
)


def _injector() -> Injector:
    # Phase 1: CommunityTaskModule now binds the ORM repos, which inject
    # DatabasePlugin. Install TestingDatabaseModule alongside it so the
    # isolated injector can resolve the DB without a full profile column.
    from agentclaw.community.di.modules.testing_database_module import (
        TestingDatabaseModule,
    )

    return Injector([TestingDatabaseModule(), CommunityTaskModule()])


def test_task_service_bound_to_real_impl():
    """Phase 2 (plan §2.5): TaskService now binds to the real impl, not Noop."""
    inj = _injector()
    svc = inj.get(TaskService)
    from agentclaw.community.core.task.services import TaskService as RealTaskService

    assert isinstance(svc, RealTaskService)
    assert isinstance(svc, TaskService)
    assert not isinstance(svc, NoopTaskService)


def test_bot_discover_port_bound():
    assert isinstance(_injector().get(BotDiscoverPort), NoopBotDiscoverPort)


def test_decomposer_port_bound():
    # Phase 4.3: DecomposerPort now binds to the rule-based DecomposerService.
    from agentclaw.community.core.task.services.decomposer_service import (
        DecomposerService,
    )

    assert isinstance(_injector().get(DecomposerPort), DecomposerService)


def test_task_driver_port_bound():
    assert isinstance(_injector().get(TaskDriverPort), NoopTaskDriverPort)


def test_execution_port_bound():
    assert isinstance(_injector().get(ExecutionPort), NoopExecutionPort)


def test_bcs_collaboration_port_bound():
    # Phase 0.9 (plan §2.4): BcsCollaborationProtocol bound to Noop for canvas
    # drill-down bring-up; Phase 4 swaps in real httpx impl.
    assert isinstance(_injector().get(BcsCollaborationProtocol), NoopBcsCollaborationPort)


def test_bindings_are_singleton():
    inj = _injector()
    assert inj.get(TaskService) is inj.get(TaskService)
    assert inj.get(ExecutionPort) is inj.get(ExecutionPort)
    assert inj.get(BcsCollaborationProtocol) is inj.get(BcsCollaborationProtocol)


def test_profile_modules_imports_task_module():
    """Guard: profile_modules.py references CommunityTaskModule (registration)."""
    from agentclaw.community.di import profile_modules
    import inspect

    src = inspect.getsource(profile_modules)
    assert "CommunityTaskModule" in src
    assert "CommunityTaskModule()" in src  # instantiated, not just imported


def test_panel_delivery_port_bound_to_noop():
    """Phase 4.5.3: community default delivery port is the Noop impl (no chat
    push bus — the frontend create-flow calls openTaskPanel directly)."""
    from agentclaw.community.api.task import PanelDeliveryPort
    from agentclaw.community.plugins.community.task.panel_carrier import (
        NoopPanelDelivery,
    )

    assert isinstance(_injector().get(PanelDeliveryPort), NoopPanelDelivery)


def test_task_panel_carrier_is_lifecycle_participant():
    """Phase 4.5.3: TaskPanelCarrier must be discoverable as a Lifecycle
    participant so the composition-root lifespan installs it at startup."""
    from agentclaw.community.kernel.lifecycle import Lifecycle
    from agentclaw.community.plugins.community.task.panel_carrier import (
        TaskPanelCarrier,
    )

    inj = _injector()
    carrier = inj.get(TaskPanelCarrier)
    assert isinstance(carrier, TaskPanelCarrier)
    assert isinstance(carrier, Lifecycle)


def test_task_panel_carrier_install_subscribes_to_event_bus():
    """Phase 4.5.3: install() subscribes the carrier to TaskPanelEvent on the
    in-process EventBus so publish → carrier → delivery port is wired."""
    from agentclaw.community.core.events.bus import EventBus
    from agentclaw.community.plugins.community.task.panel_carrier import (
        RecordingPanelDelivery,
        TaskPanelCarrier,
    )
    from agentclaw.community.plugins.community.task.panel_publisher import (
        TaskPanelEvent,
        format_task_panel_message,
    )

    bus = EventBus()
    delivery = RecordingPanelDelivery()
    carrier = TaskPanelCarrier(delivery, bus=bus)
    carrier.install(bus=bus)
    assert carrier._installed  # noqa: SLF001 — guard the install flag
    bus.publish(
        TaskPanelEvent(
            component="taskPanel.TaskWorkflowView",
            content=format_task_panel_message("task-1"),
            session_id="s-1",
        )
    )
    assert len(delivery.delivered) == 1
    assert delivery.delivered[0][0] == "s-1"