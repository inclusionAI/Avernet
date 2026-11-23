"""Pure compilation and immutable identity for managed Swarm plans."""

from hashlib import sha256
import json

from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow import (
    SwarmWorkflowDefinition,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    SwarmPlanCandidateCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.value_codec import encode


def workflow_ids(
    workflow: SwarmWorkflowDefinition, collet_id: str, task_ref: str
) -> tuple[str, dict[str, str]]:
    key = json.dumps(
        [
            collet_id,
            task_ref,
            workflow.ref.workflow_id,
            workflow.ref.version,
            workflow.digest,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    prefix = sha256(key.encode()).hexdigest()[:32]
    return f"wfp_{prefix}", {
        n.node_id: f"wfs_{prefix}_{i}" for i, n in enumerate(workflow.nodes)
    }


def compile_workflow(workflow: SwarmWorkflowDefinition, collet_id: str, task_ref: str):
    plan_id, ids = workflow_ids(workflow, collet_id, task_ref)
    depths = {}
    while len(depths) < len(workflow.nodes):
        for node in workflow.nodes:
            if node.node_id not in depths and set(node.depends_on) <= depths.keys():
                depths[node.node_id] = 1 + max(
                    (depths[d] for d in node.depends_on), default=0
                )
    width = max(sum(d == value for d in depths.values()) for value in depths.values())
    return SwarmPlanCandidateCommand(
        task_ref,
        plan_id,
        width,
        max(depths.values()),
        termination_condition={
            "workflow_ref": encode(workflow.ref),
            "workflow_digest": workflow.digest,
            "all_stages_succeeded": True,
        },
        stages=[
            {
                "stage_id": ids[n.node_id],
                "task_ref": f"wft_{ids[n.node_id][4:]}",
                "name": n.name,
                "objective": n.objective,
                "dependency_stage_refs": [ids[d] for d in n.depends_on],
                "input_artifact_refs": list(n.input_artifact_refs),
                "output_artifact_roles": [o.name for o in n.outputs],
                "completion_condition": {"contract_ref": n.completion_contract_ref},
                "workflow_node": encode(n),
                "workflow_digest": workflow.digest,
            }
            for n in workflow.nodes
        ],
    )
