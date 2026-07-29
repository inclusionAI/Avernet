"""Goal-driven task execution loop — domain models (Phase 0.1).

Pure data (dataclasses + StrEnum). No IO, no business logic, no ORM.
Tracks plan.md §1.1-§1.3: Task aggregate root with two faces
(``spec`` the intake/plan face, ``execution_graph`` the runtime face),
``AttemptedRecord`` absorbing the old RouteHop (route_class / from_mode /
to_mode / trigger folded in), and ``Node.sub_dag`` as a ``SubDagRef`` pointer
to an external cooperative-group run (plan.md §1.3a — no child state tracked;
canvas drills down via a live fetch + ``SmGraphAdapter`` at render time).

Avernet code rules: ``from __future__ import annotations`` first; use
``Optional[T]`` (never ``T | None``); required values non-optional; StrEnum
for 3.12; dataclasses for value objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


# --- enums ------------------------------------------------------------------

class TaskStatus(StrEnum):
    """Task lifecycle (plan §1.1). 8 states; 3 terminals: DELIVERED/CANCELLED/HUNG."""

    INTAKE = "intake"
    DISCUSSING = "discussing"
    PLANNED = "planned"
    EXECUTING = "executing"
    VALIDATING = "validating"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    HUNG = "hung"


class NodeStatus(StrEnum):
    """Node runtime status (plan §1.2). HUMAN_REQUIRED = 验收/终验回投需人工."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    SKIPPED = "skipped"
    HUMAN_REQUIRED = "human_required"


class GraphStatus(StrEnum):
    """ExecutionGraph placement: ON_PLAZA(自走) / AWAITING_HUMAN_* (上升后挂起) / VERIFIED."""

    ON_PLAZA = "on_plaza"
    AWAITING_HUMAN_ACCEPT = "awaiting_human_accept"
    AWAITING_HUMAN_ADJUST = "awaiting_human_adjust"
    VERIFIED = "verified"


class EdgeKind(StrEnum):
    DEPENDENCY = "dependency"
    CONDITIONAL = "conditional"
    FALLBACK = "fallback"
    PARALLEL_SYNC = "parallel_sync"


class RunMode(StrEnum):
    """Node-level execution paradigm."""

    SINGLE_BOT = "single_bot"
    COOP_GROUP = "coop_group"
    BBS = "bbs"


class CollabMode(StrEnum):
    """Coop-group internal collaboration pattern (RunMode.COOP_GROUP 时)."""

    CHAT = "chat"
    MANAGER_WORKER = "manager_worker"
    STATE_MACHINE = "state_machine"


class AcceptanceCriteriaKind(StrEnum):
    """验收标准多态 kind (properties bag 携带具体断言)."""

    INVARIANT = "invariant"
    BEHAVIOR = "behavior"
    OUTPUT = "output"
    THRESHOLD = "threshold"
    CUSTOM = "custom"


class RouteClass(StrEnum):
    """Scheduler _route 路由分级 (plan §2.2). C1~C5 由 BotDiscoverPort 推荐 + deepresearch 决策."""

    C1 = "C1"  # single-bot, straightforward
    C2 = "C2"  # single-bot, needs clarification
    C3 = "C3"  # coop-group
    C4 = "C4"  # needs runtime decomposition (sub_dag placeholder)
    C5 = "C5"  # escalate to BBS


class AttemptTrigger(StrEnum):
    """How an executor attempt was kicked off — folded into AttemptedRecord (absorbs RouteHop)."""

    ROUTED = "routed"  # initial Scheduler dispatch
    REPLANNED = "replanned"  # LOOP reroute after accept FAIL / gap
    ESCALATED_TO_BBS = "escalated_to_bbs"  # C5 上升


class AttemptOutcome(StrEnum):
    """Executor attempt outcome (owner-bot SKILL 回投 PASS/FAIL/PARTIAL)."""

    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"


class DeliverableType(StrEnum):
    CODE = "code"
    DOC = "doc"
    ARTIFACT = "artifact"
    REPORT = "report"
    DATA = "data"
    CUSTOM = "custom"


class ConstraintKind(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class TaskSource(StrEnum):
    """Where the task entered the system."""

    IM = "im"
    API = "api"
    WEB = "web"
    BCS = "bcs"
    SCHEDULER = "scheduler"


# --- spec face (intake / plan) ----------------------------------------------

@dataclass
class TaskSpecMetadata:
    id: str
    title: str
    summary: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class Constraint:
    kind: ConstraintKind
    text: str


@dataclass
class TaskContext:
    background: str = ""
    constraints: list[Constraint] = field(default_factory=list)


@dataclass
class AcceptanceCriteria:
    """Polymorphic via ``kind`` + ``properties`` bag (no subclass explosion)."""

    kind: AcceptanceCriteriaKind
    properties: dict = field(default_factory=dict)


@dataclass
class TaskGoal:
    objective: str
    acceptances: list[AcceptanceCriteria] = field(default_factory=list)


@dataclass
class Deliverable:
    type: DeliverableType
    location: str


@dataclass
class ExecutionMeta:
    """Optional pre-execution hint (owner_bot / preferred run_mode). Scheduler may override."""

    run_mode: Optional[RunMode] = None
    collab_mode: Optional[CollabMode] = None
    owner_bot: Optional[str] = None


@dataclass
class SubTaskSpec:
    """Plan-time sub-task descriptor (Plan.sub_tasks)."""

    node_id: str
    spec: str
    run_mode: Optional[RunMode] = None
    depend_on: list[str] = field(default_factory=list)


@dataclass
class EdgeSpec:
    """Plan-time edge descriptor (Plan.edges)."""

    edge_id: str
    from_node: str
    to_node: str
    kind: EdgeKind = EdgeKind.DEPENDENCY


@dataclass
class Plan:
    sub_tasks: list[SubTaskSpec] = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class TaskSpec:
    """The intake/plan face. Progressive: only metadata required at INTAKE."""

    metadata: TaskSpecMetadata
    context: TaskContext = field(default_factory=TaskContext)
    goal: Optional[TaskGoal] = None
    deliverables: list[Deliverable] = field(default_factory=list)
    execution: Optional[ExecutionMeta] = None
    plan: Optional[Plan] = None


# --- runtime face (execution graph) -----------------------------------------

@dataclass
class ArtifactRef:
    name: str
    location: str = ""
    type: str = ""


def _default_node_properties() -> dict:
    """Per-node runtime knobs (guard ceilings; Scheduler/owner-bot may bump within)."""
    return {"retry_count": 0, "max_attempts": 2, "loop_round": 0}


@dataclass
class AttemptedRecord:
    """One executor attempt on a node. Absorbs the old RouteHop
    (route_class / from_mode / to_mode / trigger) so routing history rides
    inline with the attempt — no separate hop table."""

    executor_id: str
    paradigm: RunMode
    round: int
    outcome: Optional[AttemptOutcome] = None
    route_class: Optional[RouteClass] = None
    from_mode: Optional[RunMode] = None
    to_mode: Optional[RunMode] = None
    trigger: AttemptTrigger = AttemptTrigger.ROUTED
    at: Optional[str] = None
    note: str = ""


@dataclass
class SubDagRef:
    """Pointer from a cooperative-group node to its external execution run
    (plan.md §1.3a). The task graph holds ONLY this reference — it does not
    persist the group's child node state, so the group-self-loop invariant
    (no per-child tracking) stays intact. The canvas drills down by fetching
    the live run graph (e.g. BCS ``GET /state-machine-runs/{bcs_run_id}/graph``)
    and mapping it via ``SmGraphAdapter`` at render time (路 A).

    ``workflow_yaml_snapshot`` is an audit/replay-only capture of the injected
    workflow definition; it is NOT live state and must not be used to drive the
    canvas. New ``ref_kind`` values can be added without changing the graph
    schema (NFR-EXT-01).
    """

    ref_kind: str
    bcs_run_id: str
    group_id: str
    workflow_yaml_snapshot: Optional[str] = None


@dataclass
class Node:
    node_id: str
    spec: str
    status: NodeStatus = NodeStatus.PENDING
    run_mode: Optional[RunMode] = None
    targets_acceptance: list[AcceptanceCriteria] = field(default_factory=list)
    targets_deliverable: list[Deliverable] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    attempted_executors: list[AttemptedRecord] = field(default_factory=list)
    properties: dict = field(default_factory=_default_node_properties)
    assignee: Optional[str] = None
    instruction: Optional[str] = None
    sub_dag: Optional[SubDagRef] = None  # external-run pointer (plan §1.3a); no child state tracked


@dataclass
class Edge:
    edge_id: str
    from_node: str
    to_node: str
    kind: EdgeKind = EdgeKind.DEPENDENCY


@dataclass
class TaskExecutionGraph:
    """The runtime face. ``root_phase`` mirrors the owning Task's current phase
    so a graph snapshot is self-describing. ``graph_status`` gates who can move
    next (ON_PLAZA = Scheduler/owner-bot; AWAITING_HUMAN_* = parked)."""

    root_phase: TaskStatus
    graph_status: GraphStatus = GraphStatus.ON_PLAZA
    loop_round: int = 0
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


@dataclass
class ProgressNode:
    """Read-only projection of a Node for progress reporting (event payload)."""

    node_id: str
    seq: int
    way: str
    status: NodeStatus = NodeStatus.PENDING
    external: bool = False


# --- aggregate root ---------------------------------------------------------

@dataclass
class Task:
    """Aggregate root. ``spec`` = intake/plan face; ``execution_graph`` = runtime face.
    ``latest_event_seq`` is the TaskService event-log watermark (single writer guard).
    ``status`` is the canonical lifecycle phase (8 states); ``execution_graph.root_phase``
    mirrors it so a graph snapshot is self-describing. TaskService keeps the two in sync
    on every phase move via the state_machine guard."""

    id: str
    user_id: str
    source: TaskSource
    spec: TaskSpec
    status: TaskStatus = TaskStatus.INTAKE
    execution_graph: Optional[TaskExecutionGraph] = None
    latest_event_seq: int = 0
    loop_round: int = 0
