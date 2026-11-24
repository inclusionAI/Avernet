from dataclasses import replace
import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    ParticipationFacts,
)
from agentclaw.community.core.task.context_collab.swarm_planning import PlanningRequest
from agentclaw.community.core.task.context_collab.workflow_planning import (
    WorkflowPlanningService,
)
from tests.community.core.task.context_collab.swarm_workflow_support import (
    workflow,
    AGENTS,
)


def request():
    return PlanningRequest(
        "collet",
        "root",
        "objective",
        (),
        ParticipationFacts(
            tuple(Participant(f"p{i}", None, a) for i, a in enumerate(AGENTS)), ()
        ),
    )


@pytest.mark.parametrize("index", [0, 1])
def test_planner_returns_whole_dag_and_random_eligible_executor(index):
    seen = []

    def choose(candidates):
        seen.append(candidates)
        return candidates[index]

    planner = WorkflowPlanningService(workflow(), choose)
    first, second = planner.plan(request()), planner.plan(request())
    assert first.candidate == second.candidate
    assert len(first.candidate.stages) == 4
    assert first.assignment.participant_ref == f"p{index}"
    assert len(seen) == 2 and all(len(c) == 2 for c in seen)
    assert first.candidate.max_parallelism == 2 and first.candidate.max_depth == 3


def test_planner_filters_nonparticipants_and_rejects_ambiguous_identity():
    req = request()
    single = replace(
        req, facts=replace(req.facts, participants=req.facts.participants[:1])
    )
    assert (
        WorkflowPlanningService(workflow()).plan(single).assignment.participant_ref
        == "p0"
    )
    with pytest.raises(ContextCollabValidationError):
        WorkflowPlanningService(workflow()).plan(
            replace(req, facts=replace(req.facts, participants=()))
        )
    with pytest.raises(ContextCollabValidationError, match="duplicate Participant"):
        replace(
            req.facts,
            participants=(
                req.facts.participants[0],
                replace(req.facts.participants[0], participant_id="ambiguous"),
            ),
        )
