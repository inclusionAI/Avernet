"""Managed DAG invariants shared by driver-facing and direct Method operations."""

from dataclasses import asdict, replace
from agentclaw.community.core.task.context_collab.l5_methods.value_codec import encode

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.artifact_manifest import (
    ArtifactPortBinding,
    artifact_refs,
    manifest_envelope,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    current_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.workflow_compilation import (
    compile_workflow,
    workflow_ids,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    StageState,
)


def require_candidate(workflow, collet_id, command):
    expected = compile_workflow(workflow, collet_id, command.task_ref)
    actual = asdict(command)
    actual.pop("reason")
    wanted = asdict(expected)
    wanted.pop("reason")
    if actual != wanted:
        raise ContextCollabValidationError(
            "managed plan must exactly match pinned ProductProfile DAG"
        )


def require_stage(workflow, collet_id, plan, stage):
    expected = compile_workflow(workflow, collet_id, plan.task_ref)
    entry = next((e for e in expected.stages if e["stage_id"] == stage.stage_id), None)
    if (
        entry is None
        or stage.swarm_plan_ref != expected.swarm_plan_id
        or plan.swarm_plan_id != expected.swarm_plan_id
        or plan.termination_condition != expected.termination_condition
    ):
        raise ContextCollabValidationError("Stage is outside the pinned workflow")
    raw = stage.to_dict()
    for key, value in entry.items():
        if (
            key == "input_artifact_refs"
        ):  # Runtime selected named inputs extend the authored baseline.
            if not set(value) <= set(stage.input_artifact_refs):
                raise ContextCollabValidationError("Stage lost declared input refs")
        elif raw.get(key) != value:
            raise ContextCollabValidationError(f"managed Stage changed {key}")


def freeze_inputs(workflow, collet_id, plan, stage, stages):
    require_stage(workflow, collet_id, plan, stage)
    by_id = {s.stage_id: s for s in stages}
    if any(
        by_id[d].state is not StageState.SUCCEEDED for d in stage.dependency_stage_refs
    ):
        raise ContextCollabValidationError("workflow dependencies have not succeeded")
    _, ids = workflow_ids(workflow, collet_id, plan.task_ref)
    bindings = []
    for port in stage.workflow_node.inputs:
        source = by_id[ids[port.from_node]]
        require_stage(workflow, collet_id, plan, source)
        # Only manifests committed with success are available for handoff.
        outputs = normalize_outputs(
            source, source.output_bindings, source.output_artifact_refs
        )
        found = next(b for b in outputs if b.name == port.from_output)
        bindings.append(ArtifactPortBinding(port.name, found.artifacts))
    refs = tuple(
        dict.fromkeys(
            (*stage.workflow_node.input_artifact_refs, *artifact_refs(bindings))
        )
    )
    return replace(stage, input_bindings=tuple(bindings), input_artifact_refs=refs)


def normalize_outputs(stage, bindings, refs):
    if stage.workflow_node is None:
        raise ContextCollabValidationError(
            "named output evidence requires managed Stage"
        )
    if type(bindings) is not tuple or any(
        not isinstance(b, ArtifactPortBinding) for b in bindings
    ):
        raise ContextCollabValidationError("invalid output bindings")
    by_name = {b.name: b for b in bindings}
    declarations = stage.workflow_node.outputs
    if len(by_name) != len(bindings) or set(by_name) != {o.name for o in declarations}:
        raise ContextCollabValidationError(
            "output ports must exactly match declared outputs"
        )
    normalized = tuple(by_name[o.name] for o in declarations)
    ids = [a.artifact_id for b in normalized for a in b.artifacts]
    if len(set(ids)) != len(ids) or artifact_refs(normalized) != tuple(refs):
        raise ContextCollabValidationError(
            "artifact refs must equal the ordered manifest projection"
        )
    for output, binding in zip(declarations, normalized):
        if not output.min_items <= len(binding.artifacts) <= output.max_items:
            raise ContextCollabValidationError("output artifact cardinality mismatch")
        for artifact in binding.artifacts:
            # Reparse values even if created outside a storage codec.
            ArtifactPortBinding.from_dict(binding.to_dict())
            if (
                artifact.resource_binding.resource_ref.resource_type
                != output.resource_type
            ):
                raise ContextCollabValidationError("output resource type mismatch")
    return normalized


def stage_contract_envelope(stage, allocation_ref):
    return {
        "schema_version": 1,
        "stage_ref": stage.stage_id,
        "allocation_ref": allocation_ref,
        "workflow_digest": stage.workflow_digest,
        "node": encode(stage.workflow_node),
        "input_bindings": [b.to_dict() for b in stage.input_bindings],
    }


def require_done_evidence(record, plan, stage, allocation, bindings):
    task = next((t for t in record.tasks if t.task_id == stage.task_ref), None)
    participant = next(
        (
            p
            for p in current_participation(record.events).participants
            if p.participant_id == allocation.target_ref
        ),
        None,
    )
    envelope = manifest_envelope(stage.stage_id, allocation.allocation_id, bindings)
    if (
        task is None
        or task.status != "done"
        or task.parent_task_ref != plan.task_ref
        or participant is None
        or participant.agent_ref is None
        or task.executor_refs != (participant.agent_ref,)
    ):
        raise ContextCollabValidationError(
            "workflow completion requires allocated Agent's done child Task"
        )
    starts = [
        e
        for e in record.events
        if e.task_ref == task.task_id and e.event_kind == "task.started"
    ]
    if (
        len(starts) != 1
        or starts[0].actor_ref != task.principal_ref
        or not isinstance(starts[0].payload, dict)
        or starts[0].payload.get("swarm_stage_contract")
        != stage_contract_envelope(stage, allocation.allocation_id)
    ):
        raise ContextCollabValidationError(
            "child Task lacks pinned node/input/output contract evidence"
        )
    events = [
        e
        for e in record.events
        if e.task_ref == task.task_id
        and e.event_kind in {"task.submitted", "task.accepted"}
    ]
    if len(events) != 2 or events[-1].event_id != task.last_event_ref:
        raise ContextCollabValidationError(
            "workflow completion lacks authoritative child evidence"
        )
    submitted, accepted = events
    agent = participant.agent_ref
    if (
        submitted.actor_ref is None
        or accepted.actor_ref is None
        or not isinstance(submitted.payload, dict)
        or not isinstance(accepted.payload, dict)
        or (submitted.actor_ref.principal_type, submitted.actor_ref.principal_id)
        != (agent.agent_type, agent.agent_id)
        or accepted.actor_ref != task.principal_ref
        or any(
            e.payload.get("swarm_artifact_manifest") != envelope
            or e.payload.get("artifacts") != list(artifact_refs(bindings))
            for e in events
        )
    ):
        raise ContextCollabValidationError("child Task manifest or actor mismatch")
