"""Product-specific, read-only unified plan mock. Not an L5 implementation."""

from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    SwarmPlanCandidateCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.matching import (
    stage_role_candidates,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    StageState,
)
from agentclaw.community.core.task.context_collab.swarm_planning import (
    PlanningDecision,
    PlanningRequest,
    StageAssignment,
)

PLAN = "coding_plan"
ROLES = ("requirements", "design", "coding", "verification", "pr_submission")
ARTIFACTS = (
    "requirements.md",
    "design.md",
    "calculator.py",
    "test-report.json",
    "pr-draft.json",
)
PLAN_SERVICE = "common_task_plan"


class LocalCodingPlanService:
    """On EVERY call inspect completed stages, then choose next work AND worker."""

    def __init__(self):
        self.calls: list[PlanningRequest] = []
        self.transform = lambda decision: decision

    def plan(self, request: PlanningRequest) -> PlanningDecision:
        self.calls.append(request)
        completed = {s.name for s in request.stages if s.state is StageState.SUCCEEDED}
        if completed == set(ROLES):
            return self.transform(PlanningDecision(finished=True))
        next_role = next(role for role in ROLES if role not in completed)
        index = ROLES.index(next_role)
        if not request.stages:
            refs = {
                b.role: role_binding_ref(request.collet_id, b)
                for b in request.facts.role_bindings
            }
            candidate = SwarmPlanCandidateCommand(
                task_ref=request.task_ref,
                swarm_plan_id=PLAN,
                max_parallelism=1,
                max_depth=len(ROLES),
                termination_condition={"all_stages_succeeded": True},
                stages=[
                    {
                        "stage_id": role,
                        "task_ref": f"{request.task_ref}:{role}",
                        "name": role,
                        "dependency_stage_refs": [] if i == 0 else [ROLES[i - 1]],
                        "requirement_refs": [refs[role]],
                        "input_artifact_refs": []
                        if i == 0
                        else [f"artifact://{ARTIFACTS[i - 1]}"],
                        "output_artifact_roles": [ARTIFACTS[i]],
                        "completion_condition": {"required_artifact": ARTIFACTS[i]},
                    }
                    for i, role in enumerate(ROLES)
                ],
            )
            binding = next(
                b for b in request.facts.role_bindings if b.role == next_role
            )
            participant_ref = binding.participant_id
        else:
            candidate = None
            stage = next(s for s in request.stages if s.name == next_role)
            assert stage.state is StageState.READY
            assert set(stage.dependency_stage_refs) <= {
                s.stage_id for s in request.stages if s.state is StageState.SUCCEEDED
            }
            assert all(
                s.output_artifact_refs
                for s in request.stages
                if s.name in ROLES[:index]
            )
            [participant] = stage_role_candidates(
                request.collet_id, stage, request.facts
            )
            participant_ref = participant.participant_id
        return self.transform(
            PlanningDecision(
                candidate,
                StageAssignment(
                    next_role,
                    participant_ref,
                    "coding-input/1",
                    "coding-output/1",
                ),
            )
        )
