"""L5 runtime assembly ports. Planning is an outbound capability, not L5 policy."""

from dataclasses import dataclass
from typing import Protocol

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.execution_report import (
    StageExecutionReport,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    Stage,
    SwarmExecutionProfile,
)
from agentclaw.community.core.task.context_collab.swarm_planning import (
    SwarmPlanningService,
)


@dataclass(frozen=True)
class SwarmRuntimeConfiguration:
    planner: SwarmPlanningService
    actor: ActorContext
    # Trusted runtime identity for root startup, distinct from plan coordination.
    start_actor: ActorContext
    max_events: int = 100


class SwarmRuntimeResolver(Protocol):
    """Resolve pinned configuration and trusted coordination/start identities.

    L5 knows no L6. Implementations must explicitly supply both actors; Work
    startup still passes through L4/G1/G4, never an implied role grant.
    """

    def resolve(self, collet_id: str) -> SwarmRuntimeConfiguration: ...


class StageExecutionService(Protocol):
    """Dispatch worker; worker must actively report through StageReportProtocol.

    A worker may request clarification via SwarmExecutionServiceProtocol using
    its authenticated ActorContext, return from dispatch, poll its scoped Stage
    session for the reply, then report. Reply never triggers a second execute().
    execute() must return promptly; it must not join a cross-thread report while
    the local orchestration guard is held.

    Returning from dispatch alone does not complete anything. It may return before
    a remote worker reports. No coding/artifact-specific predicates live in L5.
    """

    def execute(
        self,
        stage: Stage,
        participant: Participant,
        profile: SwarmExecutionProfile,
        allocation_ref: str,
        collet_id: str,
        binding_id: str,
    ) -> None: ...

    def accepts(self, stage: Stage, report: StageExecutionReport) -> bool: ...
