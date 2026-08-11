"""P0 — 任务目标驱动执行框架的领域模型与支撑 DTO.

权威:
- 领域模型 ``numcark0b3mamum1`` (TaskSpec/TaskExecutionGraph 体系)
- 设计 v4: ``specs/2026-08-09-task-goal-driven-execution-framework/design-review.md`` §1、§3

实现原则 (对齐 AGENTS.md):
- ``T | None`` 仅当 ``None`` 是合法域态或外部输入边界;必填字段端到端非可选。
- 全部 frozen dataclass;图谱原子变更只经 ``TaskGraphStore`` (见 graph_store.py),
  本模块的类型是不可变值对象 (变更走 ``dataclasses.replace``)。

框架侧最小扩展 (相对 ``numcark0b3mamum1``):
- ``CollabMode`` 枚举 (对齐 BCS ``GroupStrategy``): CHAT | MANAGER_WORKER | STATE_MACHINE。
- ``RuntimeInfo.collab_mode`` 一等字段 (支撑 HIT_MULTI_BOTS 动态拉群 3 模式)。
- ``AcceptanceCriteria.tag`` 复用承载 scope: NODE | SUBTREE | TASK (驱动 output_projection;零模型新增)。
- ``depth`` 为核内派生 (由 ``depends_on`` 计算),非持久字段,不入模。

v4 变更 (相对 v3):
- ``Status`` 删 ``SPAWNING`` (5 态: PENDING/RUNNING/DONE/FAILED/HUNG)。
  "委托中" = 结构派生 ``decomposition_children(node) != []`` (store 已记录),不入状态。
- MISS 信号 = ``RuntimeInfo.extend_props.miss_events: list[str]`` (append+consume),
  非状态、非标记握手。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ============================================================================
# 枚举 (Enums)
# ============================================================================


class Status(StrEnum):
    """任务节点 / 全图执行状态 (5 态,无 phase).

    v4: 删 ``SPAWNING``。"委托中" (节点已分解、委托子节点执行) 不再是节点状态,
    而是结构派生: ``TaskGraphStore.decomposition_children(node) != []``。

    状态流转 (v4):
    - ``PENDING``  → ``RUNNING`` (dispatch HIT) / ``HUNG`` (MISS 深度闸门) /
                     ``DONE`` (传播: 分解子全 DONE → 本节点 DONE)
    - ``RUNNING``  → ``DONE`` (PASS) / ``FAILED`` (FAIL+gaps) / ``HUNG`` (STUCK/超时)
    - ``FAILED``   → ``DONE`` (补救子全 PASS 传播) / ``HUNG`` (深度闸门) /
                     ``PENDING`` (人工 rollback 复位)
    - ``HUNG``     → ``RUNNING`` (升 BBS) / ``DONE`` (BBS PASS 传播) / ``PENDING`` (人工 rollback)
    - ``DONE``     → ``PENDING`` / ``FAILED`` / ``HUNG`` (人工 rollback/cascade)
    """

    PENDING = "pending"        # 已入图,等待依赖就绪 / 待分发
    RUNNING = "running"        # 已派发执行中
    DONE = "done"              # 执行完成且验收通过 (或分解子全 PASS 传播)
    FAILED = "failed"          # 验收 FAIL (带 gaps,可触发补救;无 gaps 的超时/崩溃→HUNG)
    HUNG = "hung"              # 卡住 (深度闸门/STUCK),待人工确认或升 BBS


class AcceptanceVerdict(StrEnum):
    """验收结论."""

    PASS = "pass"
    FAIL = "fail"


class CollabMode(StrEnum):
    """协作群协作模式 (框架侧扩展,对齐 BCS ``GroupStrategy``).

    - CHAT: 自由聊天群
    - MANAGER_WORKER: 主从协作群 (master 分派 worker)
    - STATE_MACHINE: 自定义协作群 (指定 workflow yaml -> state machine)
    """

    CHAT = "chat"
    MANAGER_WORKER = "manager_worker"
    STATE_MACHINE = "state_machine"


class Scope(StrEnum):
    """验收/输入读取范围;复用承载于 ``AcceptanceCriteria.tag``.

    - NODE: 仅直系父 (数据依赖) 的产出
    - SUBTREE: 子图聚合产出
    - TASK: 全图 DONE 的产出 (终验 = 全 AC tag=task 的特例)

    ``compute_output_projection`` 据此按 scope 聚合相关 DONE output,dispatch 时
    注入各 executor 作输入/验收上下文。补救产出通过 scope=TASK 的投影被下游读到,
    无需 rewire (model B: 下游在 deps 满足前未入图,补救 PASS 后下游首次就绪)。
    """

    NODE = "node"
    SUBTREE = "subtree"
    TASK = "task"


class RunMode(StrEnum):
    """节点执行模式 (Dispatcher 搜推后填入 ``RuntimeInfo.run_mode``)."""

    SINGLE_BOT = "single_bot"   # 1:1 单 bot run
    COOP_GROUP = "coop_group"   # 协作群 (含动态拉群)
    BBS = "bbs"                 # 任务广场 lease/claim 接力


class SearchOutcome(StrEnum):
    """搜推匹配结果 (BotDiscoverPort.search)."""

    HIT_SINGLE = "hit_single"        # 单 bot cover
    HIT_GROUP = "hit_group"          # 已有协作群 cover
    HIT_MULTI_BOTS = "hit_multi_bots"  # 多 bot 合 cover -> 动态拉协作群
    MISS = "miss"                     # 无 bot cover


class DispatchKind(StrEnum):
    """分发结果类型 (DispatchOutcome.kind)."""

    DISPATCHED = "dispatched"   # 已派发到 executor
    MISS = "miss"               # 搜推 MISS,交 ExecutionEngine 按 depth 裁决


class ExecutorStatus(StrEnum):
    """executor 产出结果状态 (ExecutorResult.status)."""

    DONE = "done"      # 执行完成,含验收
    STUCK = "stuck"    # 多轮 loop 无进展 / 不可恢复 -> 引擎升 HUNG -> BBS
    FAILED = "failed"  # 执行失败


# ============================================================================
# 规划面 (Specification surface)
# ============================================================================


@dataclass(frozen=True)
class Metadata:
    """任务元信息 (设计书静态说明书)."""

    id: str
    title: str
    instruction: str


@dataclass(frozen=True)
class Context:
    """任务背景与约束."""

    background: str
    constraints: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SLA:
    """服务级别协议 (超时闸门 + 优先级)."""

    timeout_ms: int
    priority: int


@dataclass(frozen=True)
class AcceptanceCriteria:
    """验收标准.

    ``tag`` 复用承载 ``Scope`` (node|subtree|task),驱动 output_projection。
    领域模型 owner 可将 ``tag`` 当通用标签;框架在使用时按 scope 语义解释,
    非法 scope 值在 ``compute_output_projection`` 校验报错,不在构造期约束。
    """

    id: str
    description: str
    tag: str


@dataclass(frozen=True)
class Goal:
    """任务目标 + 验收标准集合."""

    objective: str
    acceptances: list[AcceptanceCriteria] = field(default_factory=list)


@dataclass(frozen=True)
class TaskSpec:
    """任务规格 (静态说明书): 元信息 / 背景 / 目标 / SLA."""

    metadata: Metadata
    context: Context
    goal: Goal
    sla: SLA


# ============================================================================
# 入口 (Entry)
# ============================================================================


@dataclass(frozen=True)
class TaskInfo:
    """任务入口信息 (对外 execute 入参)."""

    task_spec: TaskSpec
    source_channel_type: str            # "bot" | "coop_group"
    source_channel_id: str              # bot_id / 协作群 id
    execution_config: dict[str, Any] = field(default_factory=dict)
    # MAX_DEPTH / 并发等旋钮;ExecutionEngine 深度闸门读 MAX_DEPTH。
    # 例: {"MAX_DEPTH": 3, "MAX_CONCURRENT": 4}


# ============================================================================
# 验收 (Acceptance)
# ============================================================================


@dataclass(frozen=True)
class AcceptanceResult:
    """验收结果 (节点执行/验收器回投).

    ``gaps`` 非空时触发补救式 plan (model B: 针对该节点产子挂它下)。
    ``gaps`` 为空 (超时/崩溃/STUCK) → HUNG,不补救。
    """

    verdict: AcceptanceVerdict
    acceptances_met: list[str] = field(default_factory=list)    # 达标的 AC id
    gaps: list[str] = field(default_factory=list)               # 未达标的 gap 描述
    verifier: str = ""                                          # 验收器标识 (bot_id / group master)


# ============================================================================
# 运行面 (Runtime surface)
# ============================================================================


@dataclass(frozen=True)
class RuntimeInfo:
    """节点运行信息 (持久运行面).

    所有 ``None`` 字段均为合法域态 (节点尚未派发/未开始/未验收),非防御性可选。
    ``depth`` 为核内派生 (由 ``depends_on`` 计算),不在此持久化。

    ``extend_props.miss_events`` (v4): ``list[str]`` 类型,dispatch MISS 时引擎
    append 事件,``plan`` 读取后产补救子,引擎随即 consume (清空)。
    append+consume 在同一 drive pass 内完成,不跨 drive 持久化。
    """

    run_mode: RunMode | None = None             # Dispatcher 搜推后填
    assignee: str | None = None                 # bot_id / group_id
    collab_mode: CollabMode | None = None       # ★ 框架侧扩展: coop_group 时填 3 模式
    start_time: float | None = None             # RUNNING 时写
    end_time: float | None = None               # 终态写
    output: dict[str, Any] = field(default_factory=dict)
    acceptance_result: AcceptanceResult | None = None
    extend_props: dict[str, Any] = field(default_factory=dict)
    # miss_events: list[str]  (dispatch MISS → plan 消费)
    # 崩溃栈/超时/dispatch loop 次数等旁路信息


# ============================================================================
# 图 (Runtime graph)
# ============================================================================


@dataclass(frozen=True)
class TaskNode:
    """图节点.

    ``depends_on`` 单字段统一承载依赖前置 (结构父 + 数据依赖同一 list,
    已确认 design Q1)。``dependencies_satisfied`` 与 ``depth`` 均据此核内派生,
    不持久化、不在此计算。

    v4: 无 ``SPAWNING`` 状态。"委托中" = ``decomposition_children(node) != []``
    (由 ``TaskGraphStore.add_task_graph`` 记录)。节点停留在原 lifecycle 状态:
    根初始拆解 → ``PENDING`` + 有子;FAIL 补救 → ``FAILED`` + 有子;MISS 拆解 →
    ``PENDING`` + 有子。分解子全 DONE → 传播顶回 ``DONE``。
    """

    node_id: str
    depends_on: list[str]
    task_spec: TaskSpec
    status: Status = Status.PENDING
    run_info: RuntimeInfo = field(default_factory=RuntimeInfo)


@dataclass(frozen=True)
class TaskExecutionGraph:
    """任务执行图 (一个 task 一张图)."""

    status: Status = Status.RUNNING            # 全图状态 (initialize_graph -> RUNNING)
    loop_round: int = 0                        # reroute 轮次
    output: dict[str, Any] = field(default_factory=dict)
    tasks: list[TaskNode] = field(default_factory=list)
    extend_props: dict[str, Any] = field(default_factory=dict)
    # 派生不持久: subtask_ids / dependencies_satisfied / depth / decomposition_children


# ============================================================================
# 支撑 DTO
# ============================================================================


@dataclass(frozen=True)
class TaskOpResult:
    """任务级操作结果 (execute / rollback / abandon_task / patch_graph_status)."""

    task_id: str
    status: Status
    seq: int


@dataclass(frozen=True)
class NodeOpResult:
    """节点级操作结果 (add_task_graph / patch_node_runtime_info / report)."""

    task_id: str
    node_id: str
    node_status: Status
    runtime_task_id: str | None = None         # 派发后才有 (None = 未派发)


@dataclass(frozen=True)
class NodeRuntimePatch:
    """节点运行信息增量补丁 (TaskGraphStore 写网关入参).

    全部可选: 这是局部更新边界,出现 ``None`` 表示不动该字段。
    """

    status: Status | None = None
    run_mode: RunMode | None = None
    assignee: str | None = None
    collab_mode: CollabMode | None = None
    output_patch: dict[str, Any] | None = None
    acceptance_result: AcceptanceResult | None = None
    extend_props_patch: dict[str, Any] | None = None


@dataclass(frozen=True)
class NodeQueryCriteria:
    """节点查询条件 (query_nodes)."""

    status: Status | None = None
    parent_node_id: str | None = None
    dependencies_satisfied: bool = False       # True: 仅返回依赖就绪的节点


@dataclass(frozen=True)
class FilterCondition:
    """看板过滤条件 (get_task_dashboard)."""

    status: Status | None = None
    node_id: str | None = None


# TaskGraphInfo = TaskExecutionGraph (看板只读投影,零新增)
TaskGraphInfo = TaskExecutionGraph


# ============================================================================
# Dispatcher / Execution 内部 DTO
# ============================================================================


@dataclass(frozen=True)
class GroupFormation:
    """动态拉协作群参数 (TaskExecution.form_coop_group 入参)."""

    collab_mode: CollabMode
    member_bots: list[str]
    lead_bot: str
    workflow_yaml: str | None = None           # STATE_MACHINE 模式必填;CHAT/MANAGER_WORKER 为 None


@dataclass(frozen=True)
class SearchResult:
    """搜推结果 (BotDiscoverPort.search).

    - HIT_SINGLE: ``bot_id`` 填命中单 bot
    - HIT_GROUP:  ``group_id`` 填已有协作群
    - HIT_MULTI_BOTS: ``group_formation`` 填动态拉群参数 (含 collab_mode,由 search 一并决出)
    - MISS: 全空
    """

    outcome: SearchOutcome
    bot_id: str | None = None
    group_id: str | None = None
    group_formation: GroupFormation | None = None


@dataclass(frozen=True)
class DispatchOutcome:
    """分发结果 (TaskDispatcher.dispatch 出参)."""

    node_id: str
    kind: DispatchKind
    runtime_task_id: str | None = None         # DISPATCHED 时填;MISS 时为 None


@dataclass(frozen=True)
class ExecutorResult:
    """executor 产出 (TaskExecution.run_* 内部产,回投经 report_task_execution).

    STUCK -> ExecutionEngine 升 HUNG (连续 N 轮无进展 / 不可恢复 / 子 SLA 时限)。
    """

    status: ExecutorStatus
    output: dict[str, Any]
    runtime_task_id: str
    acceptance_result: AcceptanceResult | None = None
