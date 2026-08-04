"""Task domain events (Phase 0.3).

Events are the *only* input to ``TaskService._apply_event`` (event-sourced fold
into Task/Node state). The repository is a single-writer appender that assigns
``seq`` via :func:`next_seq` so the event log is monotonic per task.

``reported`` distinguishes owner-bot SKILL 回投 events (ACCEPTANCE_* /
GOAL_VERIFIED / GOAL_REJECTED / NODE_FAILED by bot) from system-driven events
(Scheduler dispatch / reroute / cancel). The guard uses this to know whether a
state move was bot-asserted (must fold into acceptance/verdict) or
system-asserted (must fold into routing/dispatch).

Avernet rules: ``from __future__ import annotations``; ``Optional[T]`` not
``T | None``; required non-optional; StrEnum.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

from .models import (
    AttemptOutcome,
    AttemptTrigger,
    NodeStatus,
    RouteClass,
    RunMode,
)


class EventKind(StrEnum):
    TASK_CREATED = "task.created"
    TASK_CLARIFIED = "task.clarified"  # clarify amend(逐轮补要素);confirmed=True → DRAFTING→DEFINED(2026-08-03 合二为一)
    NODE_DISPATCHED = "node.dispatched"
    NODE_RUNNING = "node.running"
    NODE_ACCEPTED = "node.accepted"
    NODE_REJECTED = "node.rejected"
    NODE_FAILED = "node.failed"
    GOAL_VERIFIED = "goal.verified"
    GOAL_REJECTED = "goal.rejected"
    LOOP_REROUTED = "loop.rerouted"
    EXECUTION_ATTEMPTED = "execution.attempted"
    CANCELLED = "task.cancelled"
    HUNG = "task.hung"  # deprecated: no writer since task-level HUNG terminal was removed
    # v2 全生命周期图操作事件(plan §6):图结构变更 + State 写 + 判定回投 + 挂起。
    NODE_ADDED = "node.added"          # add_node
    EDGE_ADDED = "edge.added"          # add_edge
    STATE_UPDATED = "state.updated"    # update_state(纯 State 写)
    PLAN_REQUESTED = "task.plan_requested"  # EXECUTE_START 后请 owner-bot 规划(§12)
    EXEC_AGGREGATED = "node.aggregated"  # exec-aggregate 聚合验收结果(父 subtask DONE/REJECTED)
    NODE_HANG = "node.hang"            # 卡住(节点 HUNG + graph → HUMAN_REQUIRED)
    # BBS 确认/cancel 通道(plan §13/§18.1-10,经 POST /tasks/{id}/events 回投):
    BBS_CONFIRMED = "bbs.confirmed"    # 人确认升 BBS:HUMAN_REQUIRED→BBS_ACTIVE(任务级模式,不落节点)
    HANG_CANCELLED = "hang.cancelled"  # 人确认不升 → task FAILED 终态
    NODE_RELEASED = "node.released"   # BBS 接单让出/兜底收回(§10.4/§10.3):RUNNING→FAILED
                                      # 不泵 scheduler tick、不升 HUMAN_REQUIRED。outcome ∈ {handoff,lease_expired}
    # Retained on the enum so the event-log deserializer can still read historical
    # HUNG entries; new code must not emit it.


TASK_CREATED_KIND = EventKind.TASK_CREATED

# Kinds produced by owner-bot SKILL 回投 (verification/verdict). Everything else
# is system-driven (Scheduler编排 / user / human-required escalation).
_REPORTED_KINDS: frozenset[EventKind] = frozenset(
    {
        EventKind.NODE_ACCEPTED,
        EventKind.NODE_REJECTED,
        EventKind.NODE_FAILED,
        EventKind.GOAL_VERIFIED,
        EventKind.GOAL_REJECTED,
    }
)


def is_reported_kind(kind: EventKind) -> bool:
    return kind in _REPORTED_KINDS


class IllegalEventError(ValueError):
    """Raised on invariant violation (e.g. negative seq)."""


def next_seq(latest: Optional[int]) -> int:
    """Monotonic seq watermark. ``latest`` is the prior ``TaskEvent.seq`` for
    this task (None / 0 means first event). Always increments; never reuses."""
    if latest is None:
        return 1
    if latest < 0:
        raise IllegalEventError(f"seq must be >= 0, got {latest}")
    return latest + 1


# --- base -------------------------------------------------------------------

@dataclass
class TaskEvent:
    task_id: str
    seq: int
    kind: EventKind
    reported: bool = False
    payload: dict = field(default_factory=dict)
    # Wall-clock of when the event landed in the log (ISO-8601). Set by the
    # ORM repo on read (from ``gmt_create``); None for in-memory events that
    # have not been persisted/reloaded. Exposed via GET /tasks/{id}/history.
    occurred_at: Optional[str] = None

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise IllegalEventError(f"seq must be >= 0, got {self.seq}")
        # reported flag must agree with kind unless explicitly set by subclass
        # (subclasses set reported=True via class-level default override).
        if is_reported_kind(self.kind) and not self.reported:
            self.reported = True


# --- system-driven events ---------------------------------------------------

@dataclass
class TaskCreated(TaskEvent):
    kind: EventKind = TASK_CREATED_KIND
    title: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {"title": self.title, "source": self.source}


@dataclass
class TaskClarified(TaskEvent):
    kind: EventKind = EventKind.TASK_CLARIFIED
    patch: dict = field(default_factory=dict)
    confirmed: bool = False  # True = 用户确认澄清 → DRAFTING→DEFINED

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {"patch": self.patch, "confirmed": self.confirmed}


@dataclass
class NodeDispatched(TaskEvent):
    kind: EventKind = EventKind.NODE_DISPATCHED
    node_id: str = ""
    route_class: Optional[RouteClass] = None
    run_mode: Optional[RunMode] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {
            "node_id": self.node_id,
            "route_class": self.route_class,
            "run_mode": self.run_mode,
        }


@dataclass
class NodeRunning(TaskEvent):
    kind: EventKind = EventKind.NODE_RUNNING
    node_id: str = ""
    from_status: NodeStatus = NodeStatus.PENDING

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {
            "node_id": self.node_id,
            "from_status": self.from_status,
        }


@dataclass
class LoopRerouted(TaskEvent):
    kind: EventKind = EventKind.LOOP_REROUTED
    node_id: str = ""
    new_route: Optional[RouteClass] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {"node_id": self.node_id, "new_route": self.new_route}


@dataclass
class ExecutionAttempted(TaskEvent):
    kind: EventKind = EventKind.EXECUTION_ATTEMPTED
    node_id: str = ""
    executor_id: str = ""
    paradigm: Optional[RunMode] = None
    round: int = 1
    route_class: Optional[RouteClass] = None
    trigger: AttemptTrigger = AttemptTrigger.ROUTED
    outcome: Optional[AttemptOutcome] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {
            "node_id": self.node_id,
            "executor_id": self.executor_id,
            "paradigm": self.paradigm,
            "round": self.round,
            "route_class": self.route_class,
            "trigger": self.trigger,
            "outcome": self.outcome,
        }


@dataclass
class Cancelled(TaskEvent):
    kind: EventKind = EventKind.CANCELLED
    by: str = "user"
    reason: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {"by": self.by, "reason": self.reason}


@dataclass
class Hung(TaskEvent):
    # deprecated: no producer since task-level HUNG terminal was removed (spec §2).
    # Retained so the event-log deserializer can materialize historical HUNG entries.
    kind: EventKind = EventKind.HUNG
    reason: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {"reason": self.reason}


# --- owner-bot SKILL 回投 (reported) events ---------------------------------

@dataclass
class _ReportedEvent(TaskEvent):
    """Base for bot-reported events; defaults ``reported=True``."""

    reported: bool = True


@dataclass
class NodeAccepted(_ReportedEvent):
    kind: EventKind = EventKind.NODE_ACCEPTED
    node_id: str = ""
    verifier: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {"node_id": self.node_id, "verifier": self.verifier}


@dataclass
class NodeRejected(_ReportedEvent):
    kind: EventKind = EventKind.NODE_REJECTED
    node_id: str = ""
    verifier: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {"node_id": self.node_id, "verifier": self.verifier, "reason": self.reason}


@dataclass
class NodeFailed(_ReportedEvent):
    kind: EventKind = EventKind.NODE_FAILED
    node_id: str = ""
    verifier: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {"node_id": self.node_id, "verifier": self.verifier, "reason": self.reason}


@dataclass
class NodeReleased(TaskEvent):
    kind: EventKind = EventKind.NODE_RELEASED
    node_id: str = ""
    outcome: str = "handoff"  # handoff(主动让出) | lease_expired(清扫器收回)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {"node_id": self.node_id, "outcome": self.outcome}


@dataclass
class GoalVerified(_ReportedEvent):
    kind: EventKind = EventKind.GOAL_VERIFIED
    verifier: str = ""
    verdict: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {
            "verifier": self.verifier,
            "verdict": self.verdict,
            "summary": self.summary,
        }


@dataclass
class GoalRejected(_ReportedEvent):
    kind: EventKind = EventKind.GOAL_REJECTED
    verifier: str = ""
    verdict: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.payload = {
            "verifier": self.verifier,
            "verdict": self.verdict,
            "reason": self.reason,
        }