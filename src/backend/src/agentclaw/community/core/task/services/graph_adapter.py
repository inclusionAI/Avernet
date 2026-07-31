"""SmGraphAdapter — render-time mapping of a BCS state-machine run graph into a
``TaskGraphView`` subtree (Phase 4.2b, plan §1.3a/§1.3b/§1.3c).

路 A (render-time mapping): the task graph holds only a :class:`SubDagRef`
pointer; on drill-down the adapter fetches the live BCS SM run graph via
:class:`BcsCollaborationProtocol` and maps it into the SAME ``TaskGraphView``
model the top-level dynamic DAG uses — so one canvas draws both the task-level
deepresearch DAG and a cooperative group's internal SM sub-DAG, field-isomorphic
(§1.3b superset), interaction-consistent (click node → detail).

Pure + side-effect-free: ``to_sub_dag_view`` is a pure function over a snapshot
dict (no IO), so it is unit-testable without httpx. ``fetch_sub_dag_view`` is the
only IO seam (delegates to the Port). The adapter holds NO state and performs NO
writes — the group self-loop invariant (no per-child tracking) stays intact.

Status mapping per §1.3c (SM NodeStatus → task NodeStatus).
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.core.task.protocols import BcsCollaborationProtocol
from agentclaw.community.core.task.domain.models import SubDagRef
from agentclaw.community.log import get_logger

logger = get_logger()


# --- §1.3c status mapping ---------------------------------------------------

_SM_STATUS_MAP: dict[str, str] = {
    "pending": "pending",
    "ready": "pending",
    "running": "running",
    "completed": "done",
    "failed": "failed",
    "retry_scheduled": "failed",  # PARTIAL_FAILED removed (spec R9); retry-scheduled is a FAILED node with recovery room
    "skipped": "skipped",
    "aborted": "failed",
}


def _map_node_status(sm_status: Any) -> str:
    if sm_status is None:
        return "pending"
    return _SM_STATUS_MAP.get(str(sm_status), "pending")


def _map_run_status(sm_run_status: Any) -> str:
    """SM run.status → task root_phase (best-effort; the sub-DAG is a live run,
    so default to ``executing``). Aligned to the 7-state task machine (spec §2)."""
    mapping = {
        "pending": "defined",
        "running": "executing",
        "completed": "reviewing",
        "failed": "failed",
        "aborted": "failed",  # was "hung" — task-level HUNG removed; unrecoverable → FAILED
    }
    if sm_run_status is None:
        return "executing"
    return mapping.get(str(sm_run_status), "executing")


def _map_kind_to_run_mode(kind: Any) -> Optional[str]:
    """SM node.kind → task run_mode (§1.3b: kind is a subset of task modality)."""
    if kind is None:
        return None
    k = str(kind)
    return {
        "bot_task": "single_bot",
        "human_input": "single_bot",
        "manager": "coop_group",
        "group": "coop_group",
        "bbs": "bbs",
    }.get(k, "single_bot")


# --- pure mapping ----------------------------------------------------------


def to_sub_dag_view(
    snapshot: Any,
    task_id: str,
    ref: SubDagRef,
) -> dict:
    """Map a BCS ``StateMachineRunGraphView`` snapshot into a ``TaskGraphView``
    subtree dict. Pure — no IO. Field coverage per §1.3b; status per §1.3c.

    ``snapshot`` shape: ``{run, definition, nodes, edges}`` (mirrors BCS
    ``StateMachineRunGraphView``; the Noop port returns the same shape).
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    run = snap.get("run") or {}
    definition = snap.get("definition") or {}
    sm_nodes = snap.get("nodes") or []
    sm_edges = snap.get("edges") or []

    run_status = run.get("status")
    return {
        "task_id": task_id,
        "root_phase": _map_run_status(run_status),
        "graph_status": "on_plaza",
        "loop_round": 0,
        "definition_meta": {
            "name": definition.get("name"),
            "graph_mode": definition.get("graph_mode"),
            "initial_nodes": list(definition.get("initial_nodes") or []),
            "ref_kind": ref.ref_kind,
            "bcs_run_id": ref.bcs_run_id,
            "group_id": ref.group_id,
            "drill_down_live": True,
            "definition_id": definition.get("id"),
            "definition_version": definition.get("version"),
        },
        "nodes": [_to_node_view(n, run) for n in sm_nodes],
        "edges": [_to_edge_view(e, i) for i, e in enumerate(sm_edges)],
    }


def _to_node_view(sm_node: Any, run: dict) -> dict:
    n = sm_node if isinstance(sm_node, dict) else {}
    node_id = n.get("node_id") or n.get("id") or ""
    sm_status = n.get("status")
    status = _map_node_status(sm_status)
    attempt = int(n.get("attempt") or 0)
    assignee = n.get("assignee") or n.get("assignee_bot_id") or ""
    final_output = bool(n.get("final_output") or n.get("is_final_output") or False)
    artifact_text = n.get("artifact_text")
    artifacts = []
    if artifact_text:
        artifacts.append({"name": "artifact", "location": "", "type": "text", "text": artifact_text})
    if run.get("output") and final_output:
        artifacts.append({"name": "final_output", "location": "", "type": "text", "text": run.get("output")})
    judge_outputs = n.get("judge_outputs") or []
    acceptance_result = None
    if judge_outputs:
        last = judge_outputs[-1] if isinstance(judge_outputs, list) else {}
        decision = (last or {}).get("decision") if isinstance(last, dict) else None
        if decision:
            acceptance_result = str(decision)
    return {
        "node_id": node_id,
        "display_name": n.get("display_name") or node_id,
        "run_mode": _map_kind_to_run_mode(n.get("kind")),
        "collab_mode": "state_machine",
        "status": status,
        "sub_status": n.get("sub_status"),
        "attempt": attempt,
        "assignee": assignee,
        "started_at": n.get("started_at"),
        "completed_at": n.get("completed_at"),
        "is_final_output": final_output,
        "attempted_executors": _to_attempted(n, assignee, attempt),
        "artifacts": artifacts,
        "acceptance_result": acceptance_result,
        "targets_acceptance": [],
        "sub_dag_ref": None,
        "properties": {
            "retry_count": max(attempt - 1, 0),
            "error_msg": n.get("error"),
            "partial_outcome": n.get("partial_outcome"),
            "unmet_criteria": n.get("unmet_criteria"),
            "started_at": n.get("started_at"),
            "completed_at": n.get("completed_at"),
            "max_attempts": n.get("max_attempts"),
            "human_approver": n.get("human_approver"),
            "is_final_output": final_output,
            "ready": sm_status == "ready",
            "delivery_request_id": n.get("delivery_request_id"),
            "bot_delivery_run_id": n.get("bot_delivery_run_id"),
        },
    }


def _to_attempted(n: dict, assignee: str, attempt: int) -> list:
    if not assignee and attempt <= 0:
        return []
    return [
        {
            "executor_id": assignee,
            "paradigm": _map_kind_to_run_mode(n.get("kind")) or "single_bot",
            "round": attempt or 1,
            "trigger": "routed",
            "outcome": None,
            "at": n.get("started_at"),
            "note": "",
            "delivery_request_id": n.get("delivery_request_id"),
            "bot_delivery_run_id": n.get("bot_delivery_run_id"),
        }
    ]


def _to_edge_view(sm_edge: Any, idx: int) -> dict:
    e = sm_edge if isinstance(sm_edge, dict) else {}
    source = e.get("source") or e.get("from") or e.get("from_node") or ""
    target = e.get("target") or e.get("to") or e.get("to_node") or ""
    outcome = e.get("outcome")
    guard = e.get("guard")
    kind = "conditional" if outcome else "dependency"
    return {
        "edge_id": e.get("edge_id") or f"e-{idx}-{source}-{target}",
        "from_node": source,
        "to_node": target,
        "kind": kind,
        "outcome": outcome,
        "guard": guard,
    }


# --- adapter (IO seam) -----------------------------------------------------


class SmGraphAdapter:
    """Bridges :class:`BcsCollaborationProtocol` to ``TaskGraphView`` subtrees.

    Holds NO state; performs NO writes. The cooperative group keeps its
    self-loop invariant — the task graph stores only the ``SubDagRef`` pointer.
    """

    def __init__(self, bcs: BcsCollaborationProtocol) -> None:
        self._bcs = bcs

    def fetch_sub_dag_view(
        self,
        task_id: str,
        node_id: str,
        ref: SubDagRef,
    ) -> Optional[dict]:
        """Live-fetch the SM run graph + map it. Returns None if the fetch is
        empty (router → 404). Never raises on a missing run — logs + None."""
        try:
            snapshot = self._bcs.fetch_state_machine_run_graph(ref.bcs_run_id)
        except Exception:
            logger.exception(
                "[SmGraphAdapter] fetch_state_machine_run_graph failed run=%s",
                ref.bcs_run_id,
            )
            return None
        if not snapshot:
            return None
        return to_sub_dag_view(snapshot, task_id=task_id, ref=ref)


__all__ = ["SmGraphAdapter", "to_sub_dag_view"]