"""Validated execution revision construction; never edits a published ProductProfile."""

from dataclasses import replace
from hashlib import sha256

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    resolve_transition,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.base import (
    _now,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
    InteractionMode,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    PlanState,
    Stage,
    StageState,
    SwarmPlan,
    structural_violations,
)
from agentclaw.community.core.task.context_collab.l5_methods.workflow_compilation import (
    compile_workflow,
)


def transition(value, machine, event, actor):
    state = resolve_transition(
        METHOD_MACHINE_REGISTRY[machine], value.state.value, event, {}
    )
    if state is None:
        raise ContextCollabValidationError(
            f"G4 refused {event} for {value.state.value}"
        )
    return replace(
        value,
        state=type(value.state)(state),
        transitioned_from=value.state.value,
        transition_reason=event,
        transitioned_by=actor.actor.principal_id,
        transitioned_at=_now(),
    )


def build_revision(
    collet_id,
    binding_id,
    plan,
    stages,
    allocations,
    decision,
    event_ref,
    workflow,
    actor,
):
    decision.validate(stages)
    if decision.mode is InteractionMode.IGNORE:
        raise ContextCollabValidationError(
            "ignore does not create an execution revision"
        )
    if plan.state not in {PlanState.RUNNING, PlanState.PAUSED, PlanState.COMPLETED}:
        raise ContextCollabValidationError(
            "plan cannot be replanned in its current state"
        )
    affected = (
        {s.stage_id for s in stages}
        if decision.mode is InteractionMode.REPLAN_ALL
        else {decision.from_stage_ref}
    )
    while True:
        expanded = affected | {
            s.stage_id for s in stages if set(s.dependency_stage_refs) & affected
        }
        if expanded == affected:
            break
        affected = expanded
    # Only finished unaffected work is reusable. Unfinished branches have no
    # accepted output to preserve and receive fresh execution identities too.
    reusable = {
        s.stage_id: s
        for s in stages
        if s.stage_id not in affected and s.state is StageState.SUCCEEDED
    }
    revision_id = sha256(event_ref.encode()).hexdigest()[:32]

    def node_key(stage):
        return stage.workflow_node.node_id if stage.workflow_node else stage.stage_id

    metadata = {
        "revision_id": revision_id,
        "supersedes_plan_ref": plan.swarm_plan_id,
        "interaction_ref": event_ref,
        "from_stage_ref": decision.from_stage_ref,
        "reused_tasks": {node_key(s): s.task_ref for s in reusable.values()},
        "reused_stages": {node_key(s): s.stage_id for s in reusable.values()},
    }
    if workflow is not None:
        command = compile_workflow(workflow, collet_id, plan.task_ref, metadata)
        plan_id, entries = command.swarm_plan_id, command.stages
        condition = command.termination_condition
    else:
        plan_id = f"spr_{revision_id}"
        ids = {s.stage_id: f"srr_{revision_id}_{i}" for i, s in enumerate(stages)}
        entries = []
        for old in stages:
            raw = old.to_dict()
            raw.update(
                stage_id=ids[old.stage_id],
                task_ref=(
                    old.task_ref
                    if old.stage_id in reusable
                    else f"trr_{revision_id}_{len(entries)}"
                ),
                dependency_stage_refs=[ids[d] for d in old.dependency_stage_refs],
            )
            entries.append(raw)
        condition = {**plan.termination_condition, "execution_revision": metadata}
    fresh_plan = SwarmPlan(
        plan_id,
        binding_id,
        plan.task_ref,
        plan.max_parallelism,
        plan.max_depth,
        PlanState.DRAFT,
        condition,
        plan.budget_resource_refs,
    )
    fresh_plan = transition(fresh_plan, "swarm_plan", "swarm.plan.adopt", actor)
    fresh_plan = transition(fresh_plan, "swarm_plan", "swarm.plan.start", actor)
    new_stages = []
    for old, entry in zip(stages, entries, strict=True):
        if old.stage_id in reusable:
            fresh = replace(
                old,
                stage_id=entry["stage_id"],
                swarm_plan_ref=plan_id,
                dependency_stage_refs=tuple(entry["dependency_stage_refs"]),
            )
        else:
            fresh = Stage.from_dict(
                {
                    **entry,
                    "swarm_plan_ref": plan_id,
                    "state": "PENDING",
                    "output_artifact_refs": [],
                    "input_bindings": [],
                    "output_bindings": [],
                    "transitioned_from": "",
                    "transition_reason": "replan",
                    "transitioned_by": actor.actor.principal_id,
                    "transitioned_at": _now(),
                    "context_event_refs": list(
                        dict.fromkeys((*old.context_event_refs, event_ref))
                    ),
                }
            )
        new_stages.append(fresh)
    succeeded = {s.stage_id for s in new_stages if s.state is StageState.SUCCEEDED}
    new_stages = tuple(
        transition(s, "swarm_stage", "swarm.stage.ready", actor)
        if s.state is StageState.PENDING and set(s.dependency_stage_refs) <= succeeded
        else s
        for s in new_stages
    )
    violations = structural_violations(fresh_plan, list(new_stages))
    if violations or len({s.task_ref for s in new_stages}) != len(new_stages):
        raise ContextCollabValidationError(f"invalid replanned execution: {violations}")
    previous_plan = (
        transition(plan, "swarm_plan", "swarm.plan.terminate", actor)
        if plan.state in {PlanState.RUNNING, PlanState.PAUSED}
        else plan
    )
    previous_stages = tuple(
        transition(s, "swarm_stage", "swarm.stage.cancel", actor)
        if s.state in {StageState.READY, StageState.ALLOCATING, StageState.RUNNING}
        else s
        for s in stages
    )
    updated_allocations = tuple(
        transition(a, "swarm_allocation", "swarm.allocation.revoke", actor)
        if a.stage_ref in {s.stage_id for s in stages}
        and a.state is AllocationState.ACTIVE
        else a
        for a in allocations
    )
    return previous_plan, previous_stages, updated_allocations, fresh_plan, new_stages
