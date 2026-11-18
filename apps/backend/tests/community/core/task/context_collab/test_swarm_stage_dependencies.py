"""Dependency readiness is projected only via public Swarm operations and G4."""

from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    SwarmPlanOpCommand,
    SwarmStageOutcomeCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    StageState,
)
from tests.community.core.task.context_collab.swarm_profile_support import (
    AXES,
    COLLET_ID,
    active_swarm_binding,
    allocate_command,
    make_actor,
    make_swarm_service,
    plan_command,
    profile_command,
    running_plan_harness,
)


def _complete(service, binding, stage_id):
    service.allocate_stage(
        COLLET_ID,
        binding,
        allocate_command(stage_ref=stage_id, allocation_id=f"allocation_{stage_id}"),
        make_actor(),
    )
    service.complete_stage(
        COLLET_ID, binding, stage_id, SwarmStageOutcomeCommand(), make_actor()
    )


def test_fan_in_waits_for_every_dependency_and_does_not_wake_other_plan():
    store, _, lifecycle, service = make_swarm_service()
    binding = active_swarm_binding(lifecycle)
    service.declare_execution_profile(
        COLLET_ID, binding, profile_command(), make_actor()
    )
    stages = [
        {
            "stage_id": name,
            "name": name,
            "dependency_stage_refs": dependencies,
            "completion_condition": {"reviewed": True},
        }
        for name, dependencies in (
            ("left", []),
            ("right", []),
            ("join", ["left", "right"]),
        )
    ]
    service.submit_plan_candidate(
        COLLET_ID, binding, plan_command(stages=stages), make_actor()
    )
    service.submit_plan_candidate(
        COLLET_ID,
        binding,
        plan_command(
            swarm_plan_id="other",
            stages=[
                {
                    "stage_id": "other_root",
                    "name": "other",
                    "completion_condition": {"reviewed": True},
                }
            ],
        ),
        make_actor(),
    )
    for operation in (service.adopt_plan, service.start_plan):
        operation(COLLET_ID, binding, "sp_alpha", SwarmPlanOpCommand(), make_actor())
    _complete(service, binding, "left")
    assert store.get_stage(AXES, "join", binding, COLLET_ID).state is StageState.PENDING
    _complete(service, binding, "right")
    join = store.get_stage(AXES, "join", binding, COLLET_ID)
    assert join.state is StageState.READY
    assert join.transitioned_from == "PENDING"
    assert join.transition_reason == "swarm.stage.dependencies_satisfied"
    assert (
        store.get_stage(AXES, "other_root", binding, COLLET_ID).state
        is StageState.PENDING
    )


def test_completion_while_paused_defers_successor_until_resume():
    store, _, service, binding, plan = running_plan_harness()
    service.allocate_stage(COLLET_ID, binding, allocate_command(), make_actor())
    service.pause_plan(COLLET_ID, binding, plan, SwarmPlanOpCommand(), make_actor())
    service.complete_stage(
        COLLET_ID, binding, "st1", SwarmStageOutcomeCommand(), make_actor()
    )
    assert store.get_stage(AXES, "st2", binding, COLLET_ID).state is StageState.PENDING
    service.resume_plan(COLLET_ID, binding, plan, SwarmPlanOpCommand(), make_actor())
    assert store.get_stage(AXES, "st2", binding, COLLET_ID).state is StageState.READY


def test_failed_predecessor_cannot_ready_successor():
    store, _, service, binding, _ = running_plan_harness()
    service.allocate_stage(COLLET_ID, binding, allocate_command(), make_actor())
    service.fail_stage(
        COLLET_ID,
        binding,
        "st1",
        SwarmStageOutcomeCommand(reason="test failed"),
        make_actor(),
    )
    assert store.get_stage(AXES, "st1", binding, COLLET_ID).state is StageState.FAILED
    assert store.get_stage(AXES, "st2", binding, COLLET_ID).state is StageState.PENDING
