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

class NodeStatus(StrEnum):
    """Node runtime status (spec §3.3). terminal: SKIPPED. Acceptance-fail and
    execution-fail both land in FAILED (distinguished by
    ``Node.properties['acceptance_result']``, not the enum, spec R9). ``HUNG`` =
    卡住等人工(任务级 ``GraphStatus.HUMAN_REQUIRED``)。"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    HUNG = "hung"


class GraphStatus(StrEnum):
    """任务运行时状态(图自 create 起即承载;唯一权威,无跨维度复制)。plan §5.1/spec §2.1。

    状态机:``DRAFTING``→``DEFINED``(clarify confirmed)→``RUNNING``(start);
    ``RUNNING``→``HUMAN_REQUIRED``(mark hang,卡住节点 ``HUNG``);
    ``HUMAN_REQUIRED``→``BBS_ACTIVE``(人确认升 BBS;bots 读 State 自驱剩余子任务,
    非立即执行)/→``FAILED``(人不升);``RUNNING``→``REVIEWING``(全闭合 pre-BBS 终验)
    →``DONE``(pass)/→``RUNNING``(fail 回 gap);``BBS_ACTIVE``→``DONE``/``FAILED``
    (post-BBS 终验,fail 终态不回环);任一非终态→``CANCELLED``。"""

    DRAFTING = "drafting"
    DEFINED = "defined"
    RUNNING = "running"
    HUMAN_REQUIRED = "human_required"
    BBS_ACTIVE = "bbs_active"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeType(StrEnum):
    """全生命周期节点类型(动作维度 discriminator,plan §3.1/spec §7)。

    决定执行者与状态机侧门。re-route / recurse 不是独立类型:重路由复用
    ``BOT_SEARCH`` + ``DECOMPOSITION`` 节点(spec §6 n14/n17=BOT_SEARCH、
    n18=DECOMPOSITION)。三个判定(exec-accept/exec-aggregate/goal-verify)亦为
    节点(Node 维度记一笔),其效果翻实体 status 是 State 维度(plan §2 两维度)。"""

    RECOGNITION = "recognition"          # task-create(owner-bot,task-recognition-skill)
    CLARIFY = "clarify"                 # task-clarify(owner-bot;DRAFTING 内不迁态)
    EXECUTE_START = "execute_start"     # task-execute → scheduler.start(系统桥,DRAFTING→DEFINED)
    BOT_SEARCH = "bot_search"           # 搜推匹配(owner/exec/BBS bot,task-plan-skill)
    DECOMPOSITION = "decomposition"     # 任务分解(同上执行者,递归)
    DISPATCH = "dispatch"              # 派发(系统 task-scheduler)
    EXEC_ACCEPT = "exec_accept"        # 子任务验收(执行方 bot,task-exec-skill,仅判子任务 DONE)
    EXEC_AGGREGATE = "exec_aggregate"   # 中间层聚合验收(父 owner bot,读 State 聚合判父 DONE)
    GOAL_VERIFY = "goal_verify"        # 任务终验(task-owner,goal-verify-skill,判任务 DONE)


class StateSemantics(StrEnum):
    """State fold 归约语义(plan §3.2/§8.2,FR-GRAPH-03b)。"""

    MERGE = "merge"        # 深合并(dict)
    APPEND = "append"      # 追加(去重)
    OVERWRITE = "overwrite"  # 覆盖(单调,如 depth / status)


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


class WatchdogAction(StrEnum):
    """Scheduler tick 看门狗决策 (6.5). 对一个长期 RUNNING 的 node,按 tick 超时
    决定下一步:让 bot 继续(WAIT)/ 探活 bot 上报状态(PROBE)/ 重驱 bot 执行
    (REDRIVE)/ 升级(ESCALATE,标 FAILED 走 reroute/split)。bot 会因指令遵从或
    LLM 服务不稳定 hang 住,故 scheduler 须主动探活 + 重驱。tick-based 超时(无
    wall clock),状态在 ``node.properties`` (running_ticks/probe_count/redrive_count)。"""

    WAIT = "wait"  # 仍在 bot 自上报窗口内,继续等
    PROBE = "probe"  # tick 超时,探活 bot 上报状态
    REDRIVE = "redrive"  # 探活耗尽,重驱 bot 执行(开新一轮窗口)
    ESCALATE = "escalate"  # 重驱也耗尽,升级(FAILED → reroute/split)


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
    """Sub-task descriptor(DecomposerPort.decompose_subtasks 出参 / add_node 入参)。

    ``depth`` = 递归深度(根 subtask=0;每次 DECOMPOSITION 产出 children depth=父+1,
    由 DecomposerPort 按父 SubtaskState.depth+1 填,plan §3.5/§11)。"""

    node_id: str
    spec: str
    run_mode: Optional[RunMode] = None
    depend_on: list[str] = field(default_factory=list)
    depth: int = 0


@dataclass
class TaskSpec:
    """The intake face (requirement / acceptance). Progressive: only metadata
    required at DRAFTING. Decomposition is runtime(skill 经 decompose_subtasks
    产 SubTaskSpec 直接入图),NOT a frozen field on the aggregate — the bridge
    between spec (what's wanted) and ``execution_graph`` (runtime DAG) is the
    graph itself, not a persisted Plan."""

    metadata: TaskSpecMetadata
    context: TaskContext = field(default_factory=TaskContext)
    goal: Optional[TaskGoal] = None
    deliverables: list[Deliverable] = field(default_factory=list)
    execution: Optional[ExecutionMeta] = None


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
    """Node 维度:一次动作(plan §2 两维度 / §3.3)。

    ``node_type`` 决定执行者与状态侧门(plan §9)。数据面(artifacts/instruction/
    中间结果/gap)在 v2 迁到 :class:`SubtaskState`(实体维度);``artifacts`` /
    ``instruction`` 保留为过渡只读镜像(plan §15 兼容窗口),新代码读写经 State。"""

    node_id: str
    spec: str
    status: NodeStatus = NodeStatus.PENDING
    node_type: NodeType = NodeType.DISPATCH  # v2 动作多态(默认 DISPATCH 作迁移兼容,新代码显式传)
    run_mode: Optional[RunMode] = None
    targets_acceptance: list[AcceptanceCriteria] = field(default_factory=list)
    targets_deliverable: list[Deliverable] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)  # deprecated 镜像 → SubtaskState.artifacts(§15)
    attempted_executors: list[AttemptedRecord] = field(default_factory=list)
    properties: dict = field(default_factory=_default_node_properties)
    assignee: Optional[str] = None
    instruction: Optional[str] = None  # deprecated 镜像 → SubtaskState.execution_context(§15)
    sub_dag: Optional[SubDagRef] = None  # external-run pointer (plan §1.3a); no child state tracked


@dataclass
class Edge:
    edge_id: str
    from_node: str
    to_node: str
    kind: EdgeKind = EdgeKind.DEPENDENCY


@dataclass
class GapRecord:
    """结构化 gap(plan §3.2);消解 Node.properties 里的字符串 list。"""

    node_id: str
    round: int
    unmet_criteria: list[str] = field(default_factory=list)
    verdict: Optional[AttemptOutcome] = None  # FAIL / PARTIAL
    at: str = ""


@dataclass
class SubtaskState:
    """State 维度:per-subtask 实体分区(plan §3.2/§2 两维度)。

    ``retrieve-state(node_id)`` = ``TaskState.public`` + 此分区。``status`` 为实体
    生命周期状态,由动作节点 fold 驱动(DISPATCH→RUNNING / ACCEPT→DONE /
    AGGREGATE→DONE/REJECTED),经状态机 guard。watchdog 探活计数仍留 ``Node.properties``
    (§17A.7),不收编于此。"""

    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    depth: int = 0  # 递归深度(§11);根 subtask=0
    execution_context: dict = field(default_factory=dict)  # 传递数据(MERGE)
    intermediate_results: list[dict] = field(default_factory=list)  # 中间结果(APPEND)
    artifacts: list[ArtifactRef] = field(default_factory=list)  # 已产出(APPEND,按 name 去重)
    gap_records: list[GapRecord] = field(default_factory=list)  # gap 历史(APPEND)


@dataclass
class TaskState:
    """图级一等要素,SSOT(plan §3.2/§8,FR-GRAPH-03a/b/c)。承载任务级公共上下文 +
    per-subtask 实体分区。归约语义见 ``StateSemantics``。"""

    public: dict = field(default_factory=dict)  # 任务级公共上下文(MERGE):spec 摘要/全局约束/递归上限/当前 phase
    subtasks: dict[str, SubtaskState] = field(default_factory=dict)  # key=node_id


@dataclass
class GraphSnapshot:
    """图快照(回溯/断点重跑,plan §8.3/FR-GRAPH-03c)。事件日志是时间旅行源;
    此为物化 fold 缓存。"""

    task_id: str
    at_seq: int  # 快照对应的事件 seq
    graph: TaskExecutionGraph  # 含 state 的物化 fold
    taken_at: str = ""


@dataclass
class TaskExecutionGraph:
    """The runtime face (State/Node/Edge 三要素,plan §2/§3.4)。``status`` 是任务
    运行时状态的唯一权威(图自 create 起承载)。``state`` 为图级一等要素(SSOT)。"""

    status: GraphStatus = GraphStatus.DRAFTING
    loop_round: int = 0
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    state: TaskState = field(default_factory=TaskState)


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
    """Aggregate root. ``spec`` = intake face (requirement/acceptance);
    ``execution_graph`` = runtime face (the live DAG,自 create 起即承载任务运行时
    ``status``/``loop_round``/``state``)。Task 不持有执行期标量:``status``/``loop_round``
    经 ``execution_graph`` 读写;事件日志尾部以 ``event_repo.latest_seq(task_id)`` 为准。"""

    id: str
    user_id: str
    source: TaskSource
    spec: TaskSpec
    execution_graph: Optional[TaskExecutionGraph] = None
    owner_bot_id: Optional[str] = None  # task-owner 绑定(§3.6/O-7③)

    @property
    def status(self) -> "GraphStatus":
        """任务运行时状态(无存储 delegate → ``execution_graph.status``)。"""
        if self.execution_graph is not None:
            return self.execution_graph.status
        return GraphStatus.DRAFTING

    @status.setter
    def status(self, value: "GraphStatus") -> None:
        if self.execution_graph is None:
            self.execution_graph = TaskExecutionGraph(status=value)
        else:
            self.execution_graph.status = value

    @property
    def loop_round(self) -> int:
        return self.execution_graph.loop_round if self.execution_graph else 0

    @loop_round.setter
    def loop_round(self, value: int) -> None:
        if self.execution_graph is not None:
            self.execution_graph.loop_round = value
