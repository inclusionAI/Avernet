"""Outbound Swarm planning capability: plan and executor selection are one call.

Implementations live outside L5. A decision proposes a whole initial DAG and/or
one next dispatch, or declares completion. It never grants Method authority.
The caller supplies fresh authoritative stages and participation on every call.
"""

from dataclasses import dataclass
from typing import Protocol

from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    ParticipationFacts,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    SwarmPlanCandidateCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import Stage


@dataclass(frozen=True)
class PlanningRequest:
    collet_id: str
    task_ref: str
    objective: str
    stages: tuple[Stage, ...]
    facts: ParticipationFacts
    interactions: tuple[dict, ...] = ()


@dataclass(frozen=True)
class StageAssignment:
    stage_ref: str
    participant_ref: str
    input_contract_ref: str
    output_contract_ref: str


@dataclass(frozen=True)
class PlanningDecision:
    # None means an existing plan is being reconciled, not a missing initial plan.
    candidate: SwarmPlanCandidateCommand | None = None
    assignment: StageAssignment | None = None
    finished: bool = False


class SwarmPlanningService(Protocol):
    """Configured named capability; exceptions propagate and never imply success."""

    def plan(self, request: PlanningRequest) -> PlanningDecision: ...
