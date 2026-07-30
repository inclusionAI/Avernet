"""Task module contracts — Protocols + DTOs (Phase 0.5 → relocated Phase 4/5).

These contracts live in **core** (not api) so that:

- **core services** (TaskService / TaskScheduler / BbsExecutorService /
  DecomposerService / SmGraphAdapter) may depend on them as ``@inject`` param
  types without importing the api layer (four-layer rule: core must NOT import
  api).
- **plugins** (Noop impls, in-memory repos, httpx clients) may implement them
  without importing api (plugins must NOT import api).
- **api/task/** (package __init__) re-exports them so the router / DI composition root
  (which MAY import core) still references ``api.task.TaskService`` etc. as the
  DI binding keys.

Plan §2.1/§2.4 (Paradigm A — TaskScheduler-driven): TaskService is the unified
authority (query + intake + event-fold/guard); TaskDriverPort / BotDiscoverPort /
DecomposerPort / ExecutionPort are the Scheduler's orchestration seams;
BcsCollaborationProtocol is the read-only drill-down query face;
PanelEventPublisher is the副屏 popup seam (FR-OBS-11); BbsExecutor is the广场
self-drive seam (Phase 5). DTOs (BotCandidate / RouteRecommendation /
DispatchResult / PanelMessage) are the Port return contracts.

Avernet rules: ``from __future__ import annotations``; ``Optional[T]`` not
``T | None``; required non-optional; StrEnum/dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from agentclaw.community.core.task.domain.events import TaskEvent
from agentclaw.community.core.task.domain.models import (
    Plan,
    RouteClass,
    RunMode,
    Task,
)


# --- DTOs -------------------------------------------------------------------

@dataclass
class BotCandidate:
    """A bot recommended for executing a node."""

    bot_id: str
    fit_score: float = 0.0
    reason: str = ""


@dataclass
class RouteRecommendation:
    """Output of BotDiscoverPort.recommend — Scheduler's _route input."""

    route_class: RouteClass
    run_mode: RunMode
    candidates: list[BotCandidate] = field(default_factory=list)
    confidence: float = 0.0
    rationale: str = ""


@dataclass
class DispatchResult:
    """Output of TaskDriverPort.dispatch_node / ExecutionPort.* — proto for
    what got dispatched and an accept_token the owner-bot SKILL echoes back."""

    node_id: str
    executor_id: str
    run_mode: RunMode
    accept_token: str = ""
    dispatched_at: str = ""


@dataclass
class PanelMessage:
    """A secondary-panel (副屏) popup directive (plan §1.4b / FR-OBS-11).

    Mirrors the BCS ``<AixUI panel>`` message contract: ``component`` names the
    canvas to mount (``taskPanel.TaskWorkflowView`` for the task-entry dynamic
    DAG), ``params`` carries the bind key (``task_id``). TaskService publishes
    one on ``create`` so the副屏 pops the overall task execution flow at task
    creation, aligning the state-machine run-start popup behavior.
    """

    component: str
    params: dict = field(default_factory=dict)
    kind: str = "open_panel"


# --- Protocols --------------------------------------------------------------

@runtime_checkable
class TaskService(Protocol):
    """Unified task authority: query + intake + event-fold/guard (plan §2.1).

    Holds NO编排 decision. ``on_event`` is the *only* state write path: it
    folds an event through the state_machine guard into the aggregate and
    appends to the event log (single writer). Scheduler编排放入事件,
    owner-bot SKILL 回投放入事件,TaskService 一律经 ``on_event`` 落态.
    """

    # query face
    def get(self, task_id: str) -> Optional[Task]:
        """Return the task snapshot, or None if absent."""
        ...

    def list_by_user(self, user_id: str, limit: int = 50) -> list[Task]:
        """Return tasks owned by ``user_id``, newest first."""
        ...

    def progress(self, task_id: str) -> dict:
        """Return a read-only progress projection (nodes/loop_round/phase)."""
        ...

    # intake face
    def create(self, title: str, source: str = "api", background: str = "") -> Task:
        """Create a task at INTAKE; returns the new aggregate."""
        ...

    def amend(self, task_id: str, patch: dict) -> Task:
        """Amend the spec (intro>DICUSSING); returns updated aggregate."""
        ...

    def finalize_plan(self, task_id: str, plan: Plan) -> Task:
        """Freeze a Plan (DICUSSING/PLANNED → PLANNED); returns aggregate."""
        ...

    # event-fold / guard face (plan §2.1, §5.3)
    def on_event(self, event: TaskEvent) -> Task:
        """Guard via state_machine → fold into aggregate → append to log.
        Rejects illegal transitions (raises IllegalTransitionError)."""
        ...

    def claim_node(self, task_id: str, node_id: str, executor_id: str) -> DispatchResult:
        """Atomically mark a node RUNNING + record the executor attempt.
        Raises if the node is already claimed or terminal."""
        ...

    # --- history / trace face (plan §6.6) ---------------------------------
    def history(self, task_id: str, after_seq: int = 0) -> list[TaskEvent]:
        """Return the append-only event log for ``task_id`` in seq order
        (the authoritative execution trace). ``after_seq`` for incremental
        follow. Exposed via GET /tasks/{task_id}/history."""
        ...


@runtime_checkable
class BotDiscoverPort(Protocol):
    """Recommend executors + a route class for a node (Scheduler _route input)."""

    def recommend(self, task_id: str, node_id: str) -> RouteRecommendation:
        ...


@runtime_checkable
class DecomposerPort(Protocol):
    """Decompose a spec into a Plan at runtime (C4 runtime拆解 / LOOP重规划)."""

    def decompose(self, task_id: str) -> Plan:
        ...


@runtime_checkable
class TaskDriverPort(Protocol):
    """Scheduler 编排 dispatch face. Owner-bot SKILL does NOT call this."""

    def dispatch_node(self, task_id: str, node_id: str) -> DispatchResult:
        ...

    def redispatch(self, task_id: str, node_id: str, route_class: RouteClass) -> DispatchResult:
        ...

    def escalate_to_bbs(self, task_id: str, reason: str = "") -> DispatchResult:
        ...


@runtime_checkable
class ExecutionPort(Protocol):
    """Effect: actually launch executors. Impl in plugins (local/community/prod)."""

    def dispatch_single_bot(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        ...

    def coop_group(self, task_id: str, node_id: str, bot_ids: list[str]) -> DispatchResult:
        ...

    def redispatch_node(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        ...

    def probe(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        """Ask the executor to report its current status (watchdog PROBE, 6.5).

        Fire-and-forget ping — the bot may be hung (instruction-following or
        LLM-service instability); on receipt it should post ``NODE_ACCEPTED`` /
        ``NODE_FAILED`` (or a status event). Returns an ack ``DispatchResult``.
        """
        ...

    def bbs(self, task_id: str, node_id: str, reason: str = "") -> DispatchResult:
        ...


@runtime_checkable
class TaskScheduler(Protocol):
    """Orchestration authority (plan §2.1/§3). Drives the EXECUTING→VALIDATING
    loop: ``start`` (approve委派) advances PLANNED→EXECUTING + spawns the build
    DAG; ``tick`` topo-unlocks PENDING nodes, dispatches via the Ports, and
    forces VALIDATING when all nodes settle or termination guards trip;
    ``on_event`` folds编排 reactions (accept FAIL → gap reroute/split;
    NODE_FAILED → retry→reroute). Holds NO state of its own — all writes go
    through :class:`TaskService`."""

    def start(self, task_id: str) -> Task:
        ...

    def tick(self, task_id: str) -> dict:
        ...

    def on_event(self, event: TaskEvent) -> Optional[Task]:
        ...


@runtime_checkable
class BbsExecutor(Protocol):
    """BBS 广场 executor (plan §5). The shared blackboard IS the task's
    :class:`TaskExecutionGraph` — bots read via :class:`TaskService` query face
    and write via ``on_event`` (run_mode=BBS) through the state group (NO
    Scheduler tick — BBS is self-drive on the广场). This executor holds广场 /
    认领 / 续做 *mechanics* only; it holds NO task state (the graph + event log
    remain the single source of truth)."""

    def claim(self, task_id: str, bot_id: str) -> DispatchResult:
        """广场 CAS 认领:原子地把一个 PENDING 节点标 RUNNING + 记录执行方.
        Raises if no node is claimable or already claimed."""
        ...

    def post_progress(self, event: TaskEvent) -> Optional[Task]:
        """广场续做:fold a bot-reported event via TaskService.on_event (state
        group write, no Scheduler tick). BBS FAIL verdict → HUNG (handled in
        TaskService._apply_goal_verdict)."""
        ...


@runtime_checkable
class PanelEventPublisher(Protocol):
    """Publish a secondary-panel popup directive to the frontend channel.

    The community impl rides the in-process :class:`EventBus` (and, in corp, the
    real chat SSE/WS ``<AixUI panel>`` carrier — wiring TODO Phase 6). This Port
    is the seam so TaskService stays free of frontend-channel mechanics.
    """

    def publish(self, message: PanelMessage) -> None:
        ...


@runtime_checkable
class PanelDeliveryPort(Protocol):
    """Delivery seam for a formatted ``<AixUI panel>`` chat message (FR-OBS-11
    carrier transport, plan §4.5.3). The carrier subscriber hands the formatted
    content here; the impl pushes it into the chat session stream.

    Community default = :class:`NoopPanelDelivery` (the open-source profile has
    no backend→frontend chat push bus — the frontend create-flow calls
    ``openTaskPanel`` directly on the create response). Corp/transport-bridge
    wires a real chat-WS ``<AixUI panel>`` push (TODO Phase 6). This Port holds
    NO state and never blocks the create path on delivery failure.
    """

    def deliver(self, session_id: Optional[str], content: str) -> None:
        ...


@runtime_checkable
class BcsCollaborationProtocol(Protocol):
    """Read-only query face for sub-dag drill-down (plan.md §2.4 / §1.4b).

    Fetches a BCS state-machine run graph / node detail so ``SmGraphAdapter``
    can map it into a ``TaskGraphView`` subtree at render time (路 A, §1.3a).
    This Port holds NO state and performs NO writes — the cooperative group
    keeps its self-loop invariant (no per-child tracking) and the task graph
    stores only the ``SubDagRef`` pointer.

    Impl: local mock (fake SM graph for canvas bring-up) / community httpx
    (local open-source BCS) / corp httpx (prod BCS).
    """

    def fetch_state_machine_run_graph(self, bcs_run_id: str) -> Any:
        """GET /state-machine-runs/{bcs_run_id}/graph → raw SM run graph snapshot."""
        ...

    def fetch_node_detail(self, bcs_run_id: str, node_id: str) -> Any:
        """GET /state-machine-runs/{bcs_run_id}/nodes/{node_id} → node detail
        (artifact_text / judge_outputs / error / attempt / timeout)."""
        ...


__all__ = [
    "BbsExecutor",
    "BcsCollaborationProtocol",
    "BotCandidate",
    "BotDiscoverPort",
    "DecomposerPort",
    "DispatchResult",
    "ExecutionPort",
    "PanelDeliveryPort",
    "PanelEventPublisher",
    "PanelMessage",
    "RouteRecommendation",
    "TaskDriverPort",
    "TaskScheduler",
    "TaskService",
]