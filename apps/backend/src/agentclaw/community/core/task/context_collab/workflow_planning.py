"""Configured DAG planner: one ready node AND one eligible Agent per plan call."""

from random import choice
from collections import Counter
from collections.abc import Callable
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow import (
    SwarmWorkflowDefinition,
)

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.workflow_compilation import (
    compile_workflow,
    workflow_ids,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    StageState,
)
from agentclaw.community.core.task.context_collab.swarm_planning import (
    PlanningDecision,
    PlanningRequest,
    StageAssignment,
)


class WorkflowPlanningService:
    def __init__(
        self,
        workflow: SwarmWorkflowDefinition,
        choose: Callable[[tuple[Participant, ...]], Participant] = choice,
    ) -> None:
        self.workflow, self.choose = workflow, choose

    def plan(self, request: PlanningRequest) -> PlanningDecision:
        candidate = (
            compile_workflow(self.workflow, request.collet_id, request.task_ref)
            if not request.stages
            else None
        )
        # Revisions retain the pinned node graph but receive fresh Stage IDs.
        _, ids = workflow_ids(self.workflow, request.collet_id, request.task_ref)
        if request.stages:
            ids = {
                s.workflow_node.node_id: s.stage_id
                for s in request.stages
                if s.workflow_node is not None
            }
            if set(ids) != {n.node_id for n in self.workflow.nodes}:
                raise ContextCollabValidationError(
                    "runtime nodes differ from pinned workflow"
                )
        states = {s.stage_id: s.state for s in request.stages}
        if request.stages and set(states) != set(ids.values()):
            raise ContextCollabValidationError(
                "runtime stages differ from pinned workflow"
            )
        if states and all(state is StageState.SUCCEEDED for state in states.values()):
            return PlanningDecision(finished=True)
        if any(
            state
            in {
                StageState.FAILED,
                StageState.CANCELLED,
                StageState.RUNNING,
                StageState.ALLOCATING,
            }
            for state in states.values()
        ):
            raise ContextCollabValidationError(
                "workflow is failed or already in flight"
            )
        for node in self.workflow.nodes:
            if states.get(ids[node.node_id]) is StageState.SUCCEEDED:
                continue
            if not all(
                states.get(ids[d]) is StageState.SUCCEEDED for d in node.depends_on
            ):
                continue
            if states and states[ids[node.node_id]] is not StageState.READY:
                raise ContextCollabValidationError("workflow stage is not ready")
            eligible = tuple(
                p for p in request.facts.participants if p.agent_ref in node.agents
            )
            if not eligible or any(
                n != 1 for n in Counter(p.agent_ref for p in eligible).values()
            ):
                raise ContextCollabValidationError(
                    "workflow needs unambiguous participating Agent candidates"
                )
            participant = self.choose(eligible)
            if participant not in eligible:
                raise ContextCollabValidationError("invalid workflow random choice")
            return PlanningDecision(
                candidate,
                StageAssignment(
                    ids[node.node_id],
                    participant.participant_id,
                    node.input_contract_ref,
                    node.output_contract_ref,
                ),
            )
        raise ContextCollabValidationError("workflow has no executable ready node")
