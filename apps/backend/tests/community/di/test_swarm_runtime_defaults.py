"""Default Swarm wiring resolves without pretending a worker is installed."""

import pytest
from injector import Injector, InstanceProvider

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.execution_report import (
    StageExecutionReport,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.report import (
    SwarmStageReportService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.unavailable_execution import (
    UnavailableStageExecutionService,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver_contracts import (
    StageExecutionService,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    ExecutionMode,
    Stage,
    StageState,
    SwarmExecutionProfile,
)
from agentclaw.community.di.modules.task.swarm_runtime import SwarmRuntimeModule


def test_default_stage_worker_resolves_as_singleton_and_fails_closed(test_injector):
    worker = test_injector.get(StageExecutionService)
    assert type(worker) is UnavailableStageExecutionService
    assert worker is test_injector.get(StageExecutionService)
    assert test_injector.get(SwarmStageReportService).executor is worker
    stage = Stage(
        "stage",
        "plan",
        "child-task",
        "Deliver",
        StageState.RUNNING,
        {"kind": "artifact"},
    )
    participant = Participant("participant", None, AgentRef("bot", "worker"))
    profile = SwarmExecutionProfile(
        "profile",
        ExecutionMode.A2A_AGENT,
        "participant",
        "contract://input",
        ("EXECUTE",),
    )
    with pytest.raises(
        ContextCollabValidationError, match="execution capability is not configured"
    ):
        worker.execute(stage, participant, profile, "allocation", "collet", "binding")
    report = StageExecutionReport(
        "collet", "binding", "stage", "allocation", ("artifact://report",)
    )
    assert worker.accepts(stage, report) is False


def test_host_can_explicitly_replace_default_stage_worker():
    # A host owns the external worker capability, not L5 business operations.
    replacement = UnavailableStageExecutionService()

    def host_capabilities(binder):
        binder.bind(StageExecutionService, to=InstanceProvider(replacement))

    injector = Injector([SwarmRuntimeModule(), host_capabilities], auto_bind=False)
    assert injector.get(StageExecutionService) is replacement
