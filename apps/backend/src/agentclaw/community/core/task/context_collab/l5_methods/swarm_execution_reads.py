"""Authorized Stage graph/session read projections, separate from mutation logic."""

from copy import deepcopy

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    current_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_state import (
    pending_clarifications,
    session_events,
    session_ref,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    StageState,
)
from agentclaw.community.core.task.context_collab.l5_methods.workflow_compilation import (
    compile_workflow,
)


class SwarmExecutionReads:
    def stages(self, collet_id: str, binding_id: str, actor: ActorContext) -> dict:
        with self._store.swarm_guard(current_axes(), collet_id):
            record, _, stages = self._execution(collet_id, binding_id)
            owner = actor.actor == record.task.principal_ref or any(
                (a.agent_type, a.agent_id)
                == (actor.actor.principal_type, actor.actor.principal_id)
                for a in record.task.executor_refs
            )
            if owner:
                self._owner(collet_id, binding_id, actor, write=False)
                return self._project(collet_id, binding_id)
            # Workers may poll only their own live Stage sessions, never the
            # task-wide conversation/history or another Agent's questions.
            allowed = set()
            for stage in stages:
                if stage.state is not StageState.RUNNING:
                    continue
                allocations = self._store.list_allocations(
                    current_axes(), binding_id, collet_id
                )
                participants = {
                    p.participant_id: p
                    for p in current_participation(record.events).participants
                }
                if any(
                    a.stage_ref == stage.stage_id
                    and a.target_ref in participants
                    and participants[a.target_ref].agent_ref is not None
                    and (
                        participants[a.target_ref].agent_ref.agent_type,
                        participants[a.target_ref].agent_ref.agent_id,
                    )
                    == (actor.actor.principal_type, actor.actor.principal_id)
                    for a in allocations
                ):
                    self._worker(collet_id, binding_id, stage, actor)
                    allowed.add(stage.stage_id)
            if not allowed:
                raise ContextCollabValidationError(
                    "actor has no readable Swarm Stage session"
                )
            result = self._project(collet_id, binding_id)
            result["stages"] = [s for s in result["stages"] if s["stage_id"] in allowed]
            result["edges"] = [
                e
                for e in result["edges"]
                if e["from_stage_ref"] in allowed and e["to_stage_ref"] in allowed
            ]
            result["plans"], result["archived_stages"], result["notifications"] = (
                [],
                [],
                [],
            )
            return result

    def _project(self, collet_id, binding_id):
        record, plan, stages = self._execution(collet_id, binding_id)
        workflow = self._workflow_policies.workflow_for(collet_id, binding_id)
        preview = []
        if plan is None and workflow is not None:
            preview = compile_workflow(workflow, collet_id, record.task.task_id).stages
        allocations = self._store.list_allocations(
            current_axes(), binding_id, collet_id
        )
        pending = [
            e
            for s in stages
            for e in pending_clarifications(record.events, binding_id, s.stage_id)
        ]
        uncertain = {
            e.payload["result"].get("stage_ref")
            for e in record.events
            if e.event_kind == "swarm.step.completed"
            and (e.payload or {}).get("binding_id") == binding_id
            and e.payload.get("result", {}).get("dispatch_state") == "unknown"
        }
        uncertain.intersection_update(
            s.stage_id for s in stages if s.state is StageState.RUNNING
        )
        rows = []
        for stage in stages:
            own_events = session_events(record.events, binding_id, stage.stage_id)
            inherited = [
                e for e in record.events if e.event_id in stage.context_event_refs
            ]
            rows.append(
                {
                    **stage.to_dict(),
                    "session_ref": session_ref(binding_id, stage.stage_id),
                    "responsibility": stage.objective or stage.name,
                    "task_status": next(
                        (t.status for t in record.tasks if t.task_id == stage.task_ref),
                        None,
                    ),
                    "inputs": {
                        "declarations": stage.to_dict()
                        .get("workflow_node", {})
                        .get("inputs", []),
                        "bindings": [b.to_dict() for b in stage.input_bindings],
                        "artifact_refs": list(stage.input_artifact_refs),
                    },
                    "outputs": {
                        "declarations": stage.to_dict()
                        .get("workflow_node", {})
                        .get("outputs", []),
                        "bindings": [b.to_dict() for b in stage.output_bindings],
                        "artifact_refs": list(stage.output_artifact_refs),
                    },
                    "executor_refs": [
                        a.to_dict()
                        for t in record.tasks
                        if t.task_id == stage.task_ref
                        for a in t.executor_refs
                    ],
                    "reused_from_stage_ref": plan.termination_condition.get(
                        "execution_revision", {}
                    )
                    .get("reused_stages", {})
                    .get(
                        stage.workflow_node.node_id
                        if stage.workflow_node
                        else stage.stage_id
                    ),
                    "status": "WAITING_FOR_INPUT"
                    if pending_clarifications(record.events, binding_id, stage.stage_id)
                    else (
                        "RECONCILIATION_REQUIRED"
                        if stage.stage_id in uncertain
                        else stage.state.value
                    ),
                    "allocations": [
                        a.to_dict()
                        for a in allocations
                        if a.stage_ref == stage.stage_id
                    ],
                    "messages": [
                        {
                            "event_ref": e.event_id,
                            "event_kind": e.event_kind,
                            "actor": e.actor_ref.to_dict() if e.actor_ref else None,
                            **e.payload,
                        }
                        for e in (*inherited, *own_events)
                    ],
                }
            )
        if preview:
            rows = [
                {
                    **entry,
                    "state": "PENDING",
                    "status": "UNPLANNED",
                    "responsibility": entry["objective"],
                    "task_status": None,
                    "inputs": {
                        "declarations": entry["workflow_node"].get("inputs", []),
                        "bindings": [],
                        "artifact_refs": entry["input_artifact_refs"],
                    },
                    "outputs": {
                        "declarations": entry["workflow_node"].get("outputs", []),
                        "bindings": [],
                        "artifact_refs": [],
                    },
                    "executor_refs": [],
                    "reused_from_stage_ref": None,
                    "session_ref": session_ref(binding_id, entry["stage_id"]),
                    "allocations": [],
                    "messages": [],
                }
                for entry in preview
            ]
        return deepcopy(
            {
                "collet_id": collet_id,
                "binding_id": binding_id,
                "version": record.collet.version,
                "plan_ref": plan.swarm_plan_id if plan else None,
                "status": "WAITING_FOR_INPUT"
                if pending
                else (
                    "RECONCILIATION_REQUIRED"
                    if uncertain
                    else (plan.state.value if plan else "UNPLANNED")
                ),
                "stages": rows,
                "edges": [
                    {"from_stage_ref": dep, "to_stage_ref": s["stage_id"]}
                    for s in rows
                    for dep in s["dependency_stage_refs"]
                ],
                "archived_stages": [
                    s.to_dict()
                    for s in self._store.list_stages(
                        current_axes(), binding_id, collet_id
                    )
                    if plan and s.swarm_plan_ref != plan.swarm_plan_id
                ],
                "plans": [
                    p.to_dict()
                    for p in self._store.list_plans(
                        current_axes(), binding_id, collet_id
                    )
                ],
                "notifications": [
                    {"event_ref": e.event_id, **e.payload} for e in pending
                ],
            }
        )
