"""HTTP request/response schemas for the task module (Phase 0.6).

Pydantic v2 models. ``patch`` / ``payload`` are kept as
loose ``dict`` at the adapter boundary; the service layer parses them into
domain dataclasses. This keeps the wire schema stable while the domain model
evolves.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# --- requests ---------------------------------------------------------------

class CreateTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, description="Task title (non-empty).")
    source: str = Field("api", description="Entry channel: im / api / web / bcs.")
    background: str = Field("", description="Optional context background.")


class ClarifyTaskRequest(BaseModel):
    patch: dict = Field(default_factory=dict, description="Spec patch merged into the task.")
    confirmed: bool = Field(
        False,
        description="True = 用户确认澄清 → DRAFTING→DEFINED(最终一轮);False = 逐轮 amend,留 DRAFTING。",
    )


class EventReportRequest(BaseModel):
    """Owner-bot SKILL 回投 envelope. ``kind`` is an EventKind value; ``payload``
    carries event-specific fields (node_id / verifier / verdict / reason ...)."""

    kind: str = Field(..., description="Event kind, e.g. 'node.accepted', 'goal.verified'.")
    seq: Optional[int] = Field(None, description="Optional seq hint; repo assigns if absent.")
    payload: dict = Field(default_factory=dict)


# --- responses --------------------------------------------------------------

class TaskCreatedResponse(BaseModel):
    task_id: str
    status: str
    seq: int


class NodeProgressItem(BaseModel):
    node_id: str
    status: str
    run_mode: Optional[str] = None


class TaskDetailResponse(BaseModel):
    task_id: str
    user_id: str
    status: str
    spec: dict = Field(default_factory=dict)
    execution_graph: Optional[dict] = None
    loop_round: int = 0
    nodes: list[NodeProgressItem] = Field(default_factory=list)


class TaskProgressResponse(BaseModel):
    task_id: str
    status: str
    loop_round: int = 0
    done: int = 0
    total: int = 0
    nodes: list[NodeProgressItem] = Field(default_factory=list)


class TaskListItem(BaseModel):
    task_id: str
    user_id: str
    status: str
    loop_round: int = 0


class TaskListResponse(BaseModel):
    items: list[TaskListItem] = Field(default_factory=list)
    total: int = 0


class EventReportResponse(BaseModel):
    task_id: str
    accepted: bool = True
    seq: int = 0


# --- canvas (secondary panel) dynamic-workflow views (Phase 0.6, §1.4b) -----
# These are the wire contract for the new dynamic-workflow canvas. They are a
# SUPERSET of the state-machine canvas fields (AC-12, §1.3b): every SM canvas
# field has a landing here; deepresearch-DAG-only fields (run_mode/collab_mode/
# attempted_executors/acceptance_result/...) extend it. Loose typing (str for
# status/sub_status/run_mode/collab_mode/edge kind) keeps the wire schema stable
# while the domain enum set evolves; the service layer maps domain -> wire.


class SubDagRefView(BaseModel):
    """Pointer from a cooperative-group node to its external execution run
    (plan §1.3a). The canvas drills down by fetching the live run graph."""

    ref_kind: str
    bcs_run_id: str
    group_id: str
    workflow_yaml_snapshot: Optional[str] = None


class TaskNodeView(BaseModel):
    """One execution node in the dynamic DAG. Supersets SM canvas node fields."""

    node_id: str
    display_name: str
    run_mode: Optional[str] = None
    collab_mode: Optional[str] = None
    status: str = "pending"
    sub_status: Optional[str] = None
    attempt: Optional[int] = None
    assignee: Optional[str] = None
    started_at: Optional[int] = None
    completed_at: Optional[int] = None
    is_final_output: bool = False
    # deepresearch-DAG superset fields
    attempted_executors: list[Any] = Field(default_factory=list)
    artifacts: list[Any] = Field(default_factory=list)
    acceptance_result: Optional[Any] = None
    properties: dict[str, Any] = Field(default_factory=dict)
    sub_dag_ref: Optional[SubDagRefView] = None


class TaskEdgeView(BaseModel):
    """Edge in the DAG. outcome/guard align the SM canvas conditional edges."""

    edge_id: str
    from_node: str
    to_node: str
    kind: str = "dependency"
    outcome: Optional[str] = None
    guard: Optional[str] = None


class TaskGraphView(BaseModel):
    """Top-level dynamic-workflow graph snapshot consumed by the canvas."""

    task_id: str
    status: str = "drafting"
    loop_round: int = 0
    definition_meta: Optional[dict[str, Any]] = None
    nodes: list[TaskNodeView] = Field(default_factory=list)
    edges: list[TaskEdgeView] = Field(default_factory=list)


class TaskNodeDetailView(BaseModel):
    """Point-node detail view (aligns SM canvas node-detail panel). Carries
    the full node superset + delivery correlation in properties."""

    node_id: str
    display_name: Optional[str] = None
    status: Optional[str] = None
    sub_status: Optional[str] = None
    attempt: Optional[int] = None
    run_mode: Optional[str] = None
    collab_mode: Optional[str] = None
    assignee: Optional[str] = None
    attempted_executors: list[Any] = Field(default_factory=list)
    artifacts: list[Any] = Field(default_factory=list)
    intermediate_results: list[dict] = Field(default_factory=list)
    gap_records: list[dict] = Field(default_factory=list)
    acceptance_result: Optional[Any] = None
    properties: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class TaskEventItem(BaseModel):
    """One row of the append-only event log (the authoritative execution trace).

    ``seq`` is monotonic per task (single-writer); ``occurred_at`` is the
    wall-clock the event landed in ``ac_task_event`` (ISO-8601, from
    ``gmt_create``); ``reported`` distinguishes owner-bot 回投 from
    system-driven events.
    """

    seq: int
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    reported: bool = False
    occurred_at: Optional[str] = None


class TaskHistoryResponse(BaseModel):
    """Seq-ordered event log for a task — exposed via GET /tasks/{id}/history."""

    task_id: str
    items: list[TaskEventItem] = Field(default_factory=list)
    total: int = 0


# --- BBS self-drive claim/release (plan §10.1/§10.4) ------------------------
# Wire contract for POST /nodes/{node_id}/claim + /release. ``run_mode`` is a
# plain string on the wire (SINGLE_BOT | COOP_GROUP | BBS) — the router passes
# it straight through to the service, which coerces it to the domain enum. This
# keeps the adapter layer free of core imports (layering invariant).


class ClaimRequest(BaseModel):
    executor_id: str
    run_mode: str = "bbs"  # SINGLE_BOT | COOP_GROUP | BBS;BBS 自主接单默认 bbs


class ClaimResponse(BaseModel):
    node_id: str
    executor_id: str
    run_mode: str
    accept_token: str = ""
    lease_until: Optional[str] = None


class ReleaseRequest(BaseModel):
    executor_id: str
    idempotency_key: Optional[str] = None


class ReleaseResponse(BaseModel):
    node_id: str
    status: str
    outcome: str  # handoff