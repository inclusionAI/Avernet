"""任务目标驱动执行框架领域模型(对齐最新 classDiagram 2026-08-11)。

权威源:`src/backend/specs/2026-08-09-task-goal-driven-execution-framework/plan.md §2`。
本模块为 shared kernel:纯 dataclass/enum + 中间类型,零依赖(不 import transport/框架)。
结构归属由 ``Relation{type=DEPENDENCY}`` 分解树(单入)表达,``TaskNode`` 不持
``decomposed_by``/``depends_on``;``depth``/结构子/结构父均从 ``relations`` 派生。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ===== 枚举 =====
class Status(StrEnum):
    """任务/节点生命周期状态(8 态, DONE 表示执行完成未通过验收, SUCCESS 表示执行完成且验收通过)。"""

    PENDING = "PENDING"      # 待处理
    PLANNING = "PLANNING"    # 规划中(被分解委托子执行,显式委托态)
    RUNNING = "RUNNING"      # 运行中
    DONE = "DONE"            # 执行完成,但尚未通过验收
    SUCCESS = "SUCCESS"      # 执行完成且已通过验收
    FAILED = "FAILED"        # 执行或验收失败(带 gaps)
    HUNG = "HUNG"            # 已挂起/暂停(仅 stuck:迭代达上限执行不下去,需人介入)
    CANCELLED = "CANCELLED"  # 已取消


class AcceptanceVerdict(StrEnum):
    """验收结论。``verdict`` 使用 ``DONE``(通过) / ``FAILED``(未通过);节点 ``status`` 则分别为 ``SUCCESS`` / ``DONE``。"""

    DONE = "DONE"
    FAILED = "FAILED"

    @classmethod
    def _missing_(cls, value: object) -> "AcceptanceVerdict | None":
        """向后兼容:历史库数据/旧上报中的 ``PASS``/``FAIL`` 自动归一到新枚举。

        覆盖所有 ``AcceptanceVerdict(value)`` 构造点(repository serializers/types 反序列化、
        callback_adapter/schemas 上报解析),使历史 ``PASS``/``FAIL`` 字面量不再抛 ValueError。
        单向归一,不回写旧值;真正非法值仍按 Enum 默认抛错。
        """
        if isinstance(value, str):
            if value == "PASS":
                return cls.DONE
            if value == "FAIL":
                return cls.FAILED
        return None


class RelationType(StrEnum):
    """节点间关系类型。"""

    DEPENDENCY = "DEPENDENCY"   # 分解树边(承载结构归属,单入)


class NodeAction(StrEnum):
    """节点动作级事件类型(append-only 历史快照;纯可观测,不入状态机驱动)。"""

    PLAN = "plan"               # 规划(gap 计算 + 产子);payload: target/children/has_gap/gap_detail
    DISPATCH = "dispatch"       # 搜推派发结果;payload: outcome(HIT_SINGLE|HIT_MULTI|MISS)/run_mode/assignee/miss_reason
    EXECUTE = "execute"         # 执行产出(bot 回投 output/exec_error);payload: success/exec_error/output
    VERIFY = "verify"           # 验收结论;payload: verdict/done_items/gap_items
    RESET = "reset"             # harness 复位重投;payload: reason/prev_status/harness_retries
    TRANSITION = "transition"   # 框架直驱翻态(HUNG/传播 DONE);payload: reason


class TaskSourceType(StrEnum):
    """触发渠道类型(bot / 协作群 / 开放 API)。"""

    BOT = "bot"
    COOP_GROUP = "coop_group"
    API = "api"


class TaskType(StrEnum):
    """任务类型(static-single-workflow / static-group-workflow / dynamic)。"""

    # Legacy task-type names remain accepted by the task API.
    YAML = "yaml"
    WORKFLOW = "workflow"
    DYNAMIC = "dynamic"
    STATIC_PLAN = "static_plan"
    BBS = "bbs"


# ===== 规格面(Task Specification)=====
@dataclass
class Context:
    """任务业务上下文。title/background 可为空，extend_props 承载交付物、约束和资源。"""

    background: str = ""
    extend_props: dict[str, Any] = field(default_factory=dict)
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "background": self.background,
            "extend_props": dict(self.extend_props),
        }


@dataclass
class AcceptanceCriteria:
    id: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "description": self.description}


@dataclass
class Goal:
    objective: str
    acceptances: list[AcceptanceCriteria]

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "acceptances": [a.to_dict() for a in self.acceptances],
        }


@dataclass
class TaskSpec:
    """纯业务规格：不承载 task_id、instruction、assignee 或其它运行态。"""

    context: Context
    goal: Goal

    def to_dict(self) -> dict[str, Any]:
        return {"context": self.context.to_dict(), "goal": self.goal.to_dict()}


def task_spec_title(spec: "TaskSpec") -> str:
    return str(spec.context.title or spec.goal.objective or "").strip()


def task_spec_instruction(spec: "TaskSpec") -> str:
    """Build an execution instruction from business facts without storing one in TaskSpec."""
    parts = [str(spec.goal.objective or "").strip()]
    props = spec.context.extend_props or {}
    for label, key in (("交付物", "deliverables"), ("约束", "constraints"), ("资源", "resources")):
        values = props.get(key) or []
        if isinstance(values, list) and values:
            parts.append(f"{label}: " + "；".join(str(item) for item in values))
    if spec.context.background:
        parts.append(f"背景: {spec.context.background}")
    return "\n".join(part for part in parts if part)


@dataclass
class TaskInfo:
    """正式任务信息；task_id 属于任务实体，不属于 TaskSpec。"""

    task_id: str
    task_spec: TaskSpec
    source_type: str       # "bot" | "coop_group"
    owner_bot_id: str         # owning bot id
    owner_user_id: str = ""   # owning user id, kept separate from owner_bot_id
    execution_config: dict[str, Any] = field(default_factory=dict)  # 指定 bot/workflow yaml/MAX_DEPTH 等


@dataclass(frozen=True)
class TaskRuntimeProfile:
    """Task-creation snapshot of governed orchestration strategies and modes."""

    planner_strategy: str = "default"
    dispatcher_strategy: str = "default"
    runner_strategy: str = "default"
    allowed_run_modes: tuple[str, ...] = ("single_bot", "coop_group", "bbs")

    @classmethod
    def from_execution_config(cls, config: dict[str, Any]) -> "TaskRuntimeProfile":
        raw = config.get("runtime_profile") or {}
        if not isinstance(raw, dict):
            raw = {}
        modes = raw.get("allowed_run_modes", config.get("allowed_run_modes"))
        if not isinstance(modes, (list, tuple)) or not modes:
            modes = cls.allowed_run_modes
        normalized = tuple(
            dict.fromkeys(str(mode).strip() for mode in modes if str(mode).strip())
        )
        return cls(
            planner_strategy=str(raw.get("planner_strategy") or "default"),
            dispatcher_strategy=str(raw.get("dispatcher_strategy") or "default"),
            runner_strategy=str(raw.get("runner_strategy") or "default"),
            allowed_run_modes=normalized or cls.allowed_run_modes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "planner_strategy": self.planner_strategy,
            "dispatcher_strategy": self.dispatcher_strategy,
            "runner_strategy": self.runner_strategy,
            "allowed_run_modes": list(self.allowed_run_modes),
        }


# ===== 运行态(Runtime Graph)=====
@dataclass
class AcceptanceResult:
    """验收/审计结果(无 verifier 字段)。"""

    verdict: AcceptanceVerdict
    done_items: list[Any] = field(default_factory=list)  # 已完成接收项;协议为对象数组[{id,passed,summary}]
    gap_items: list[Any] = field(default_factory=list)  # 未完成/未通过接收项;协议为对象数组,放宽为 Any


@dataclass
class DoneOutput:
    """Graph 已接纳、可供后续规划复用的节点执行事实。"""

    node_id: str
    actual_goal: Goal
    output: dict[str, Any]
    acceptance_result: AcceptanceResult


@dataclass
class TaskContext:
    """跨中心化与 Relay 共用的最小任务业务上下文。"""

    spec: TaskSpec
    all_done_output: list[DoneOutput]
    gaps: list[str]


@dataclass
class NodeActionEvent:
    """节点动作级历史快照(append-only;纯可观测回溯/BBS 上下文聚合,不入驱动逻辑)。

    单值字段(output/acceptance_result/start_time 等)是「当前态/最新态」,
    编排核只读单值;``action_log`` 是「动作轨迹」,按动作发生顺序只增不覆盖。
    """

    seq: int                              # 节点内自增序号(1-based;由 SSOT 网关 append 时填)
    ts: int                               # 动作发生时间戳(毫秒,由网关填 int(time.time()*1000))
    action: NodeAction
    loop_round: int = 0                   # 图级 loop_round 快照(定位第几轮)
    attempt: int = 0                      # planning/执行重试序号(harness_retries 快照)
    status_from: Status | None = None     # 动作发生前态
    status_to: Status | None = None       # 动作发生后态(None=未翻态,如纯 plan)
    payload: dict[str, Any] = field(default_factory=dict)  # 动作产出全量(按 action 类型)


@dataclass
class RuntimeInfo:
    """节点运行时实时执行信息(所有 None 均合法域态)。

    单值字段(output/acceptance_result/start_time 等)= 当前态/最新态(编排核只读驱动);
    ``action_log`` = 动作级历史快照(append-only,默认不序列化,诊断页 include_action_log 开)。
    """

    run_mode: str | None = None              # "single_bot"/"coop_group"/"bbs";无 collab_mode
    assignee: str | None = None              # 执行者(bot_id / group_id)
    start_time: int | None = None         # 任务/节点开始时间(根在 init_graph;叶子 task_dispatch/BBS claim 时写)
    end_time: int | None = None           # 进终态时写(毫秒,int(time.time()*1000))
    actual_goal: Goal | None = None          # 实际执行目标
    output: dict[str, Any] = field(default_factory=dict)
    acceptance_result: AcceptanceResult | None = None
    progress_reason: str | None = None  # why this node/assignee was advanced
    failure_reason: str | None = None   # why planning/search/delivery/execution failed
    extend_props: dict[str, Any] = field(default_factory=dict)  # miss_events/崩溃栈/超时/hung_reason(stuck)
    action_log: list[NodeActionEvent] = field(default_factory=list)  # 动作级历史快照(append-only)


@dataclass
class Relation:
    """分解树边(一等公民);承载结构归属,单入(每非根节点恰好 1 入边=结构父)。"""

    src_id: str                   # 结构父(分解源/被依赖)
    dst_id: str                   # 结构子(分解产物/依赖方)
    type: RelationType = RelationType.DEPENDENCY
    extend_props: dict[str, Any] = field(default_factory=dict)


def effective_run_mode(node: TaskNode) -> str | None:
    """Return the authoritative execution mode for a task node.

    ``actual_run_mode`` is an execution/session-permission override introduced
    by the task runtime. Empty or missing values preserve legacy ``run_mode``.
    """
    runtime = getattr(node, "run_info", None)
    if runtime is None:
        return None
    actual = runtime.extend_props.get("actual_run_mode")
    if actual is not None and str(actual).strip():
        return str(actual).strip()
    mode = runtime.run_mode
    return str(mode).strip() if mode is not None and str(mode).strip() else None


@dataclass
class TaskNode:
    """任务节点。结构归属由 ``graph.relations`` 分解树表达(无 decomposed_by/depends_on)。"""

    node_id: str                  # 节点唯一实例 ID
    task_id: str                  # 节点所发整体任务 ID(归属键)
    status: Status
    task_spec: TaskSpec
    run_info: RuntimeInfo
    node_run_graph: "TaskExecutionGraph"   # 节点所属执行图实例引用
    # 结构父/结构子查询、验收归属、传播一律从 graph.relations 分解树派生;
    # 无跨兄弟/跨层级直接数据边——数据流由步进式批规划顺序 + 执行时结构父聚合上下文承载


def _relay_graph_status(graph: "TaskExecutionGraph") -> "Status":
    """Derive the Relay graph state from baton convergence, not the root node.

    ``DONE`` on a handed-off baton only means that it produced a successor.
    The whole Relay graph is complete only after every node has reached its own
    terminal state, the active leaf is accepted (``SUCCESS``), and the latest
    persisted GAP snapshot is an explicit empty list. A missing GAP snapshot is
    not treated as completion.
    """
    if graph.status in {Status.FAILED, Status.HUNG, Status.CANCELLED}:
        return graph.status
    if not graph.tasks:
        return graph.status

    active = {Status.PENDING, Status.PLANNING, Status.RUNNING}
    if any(node.status in active for node in graph.tasks):
        return Status.RUNNING
    for terminal in (Status.FAILED, Status.HUNG, Status.CANCELLED):
        if any(node.status == terminal for node in graph.tasks):
            return terminal

    outgoing = {
        relation.src_id
        for relation in graph.relations
        if relation.type == RelationType.DEPENDENCY
    }
    leaves = [node for node in graph.tasks if node.node_id not in outgoing] or list(
        graph.tasks
    )
    all_nodes_closed = all(
        node.status in {Status.DONE, Status.SUCCESS} for node in graph.tasks
    )
    active_leaf_accepted = all(node.status == Status.SUCCESS for node in leaves)
    gaps = graph.extend_props.get("gaps")
    if gaps == [] and all_nodes_closed and active_leaf_accepted:
        return Status.DONE
    return Status.RUNNING


@dataclass
class TaskExecutionGraph:
    """任务运行时执行图。"""

    run_id: int                   # 运行实例唯一 ID
    loop_round: int               # 图级总轮次(根 gap 不闭重 plan + 升 BBS 时 ++;达 MAX_LOOP→图 HUNG)
    status: Status
    output: dict[str, Any] = field(default_factory=dict)
    tasks: list[TaskNode] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)   # 依赖关系(分解树,一等公民)
    extend_props: dict[str, Any] = field(default_factory=dict)
    execution_graph: dict[str, Any] | None = None  # 回调审计图快照(BCN/ClawMind DAG,按 session_id 反查挂图级;只读投影)
    task_id: str = ""   # 整图所属任务 ID(initialize 透传;query_task_dashboard 子树投影复制;
                        # 供执行 adapter、回调投影和 BBS 图查询使用)
    # 派生不持久: depth / child_tasks / parent_task(均从 relations 分解树派生)

    @property
    def is_relay(self) -> bool:
        """Whether this graph is driven by the serial Relay orchestration mode."""
        config = self.extend_props.get("execution_config")
        return isinstance(config, dict) and config.get("orchestration_mode") == "relay"

    @property
    def effective_status(self) -> "Status":
        """图级有效态：按编排模式派生。

        中心化保持既有口径：有根节点时以根节点状态为准。Relay 不镜像根节点；
        根/前序节点 ``DONE`` 只表示已交接，图级状态由各节点收敛态与最新 GAP 决定。
        无根(未初始化)回落存储的图级 ``status``。

        纯只读派生，不改并发主线。图级 ``status`` 仍由 Relay 事实写入或
        ``update_task_graph_info`` 显式写；控制流(``_is_graph_terminal`` 等)继续读 ``status``。
        本属性供持久化、任务列表和看板等统一观测口径消费。"""
        if self.is_relay:
            return _relay_graph_status(self)
        root = next((n for n in self.tasks if n.node_id == self.task_id), None)
        return root.status if root is not None else self.status



@dataclass
class TaskSummary:
    """任务摘要(列表视图轻量投影;非完整图)。``list_task_summaries`` 返回项。"""

    task_id: str
    run_id: int
    status: Status
    title: str = ""              # 根节点 task_spec.context.title
    node_count: int = 0          # 图中节点总数
    loop_round: int = 0          # 图级轮次
    bbs_mode: bool = False       # 图 extend_props["bbs_mode"] 投影(BBS-relay 升级标志)

# ===== 中间类型(patch/criteria/op_result/callback)=====
@dataclass
class TaskNodePatch:
    """节点级原子写(``update_task_node_info`` 入参)。

    终态翻转三选一(互斥):　
    ① ``acceptance_result`` 非空 → 验收驱动:PASS→SUCCESS / FAIL→DONE(验收未通过仅记录结论,gaps 可空);　
    ② ``exec_error`` 非空 → 执行报错(bot 压根没跑通:run FAILED / SLA 超时 / poll 耗尽),
       不翻终态,由编排核 on_harness 复位重投(计数,达上限→HUNG);　
    ③ ``status`` 非空(无前两者)→ 框架直驱(PENDING→RUNNING 派发 / RUNNING→PENDING harness 复位 等)。　
    三者全空 → 仅 fold 非状态字段(output/run_mode/assignee/extend_props)。
    """

    task_id: str
    node_id: str
    status: Status | None = None
    run_mode: str | None = None
    assignee: str | None = None
    start_time: int | None = None                    # 节点进入 task_dispatch/BBS claim 的时间
    output_patch: dict[str, Any] | None = None               # fold 到 run_info.output
    acceptance_result: AcceptanceResult | None = None        # 验收驱动终态翻转(DONE→SUCCESS / FAILED→DONE)
    exec_error: str | None = None                            # 执行报错信号(非验收;→ on_harness 重投,)
    progress_reason: str | None = None                       # 推进原因(规划/搜推/派发)
    failure_reason: str | None = None                        # 失败原因(规划/搜推/派发/执行)
    extend_props_patch: dict[str, Any] | None = None         # miss_events / hung_reason(stuck) / harness_retries / 崩溃栈
    actual_goal: Goal | None = None                            # Relay 当前 Bot 实际接受的执行目标
    local_acceptance_result: AcceptanceResult | None = None   # Relay 节点局部验收事实，不直接驱动终态
    execution_decision: str | None = None                      # ACCEPTED | DECLINED


@dataclass
class TaskGraphPatch:
    """图级原子写(``update_task_graph_info`` 入参);收口图级终态(图 ``status``/``loop_round``/``output``/``extend_props``)。

    所有字段可选(增量 patch):未给的字段不动。``loop_round_increment`` 非空时执行原子加(默认 +1);
    ``status`` 非空时置图级终态;``output_patch`` 浅合并到图 ``output``;``extend_props_patch`` 浅合并到图
    ``extend_props``(承载 ``bbs_mode``/``hung_reason`` 等)。
    """

    loop_round_increment: int | None = None
    status: Status | None = None
    output_patch: dict[str, Any] | None = None
    extend_props_patch: dict[str, Any] | None = None


@dataclass
class TaskNodeQueryCriteria:
    """节点查询条件(内部用)。"""

    status: Status | None = None
    node_ids: list[str] | None = None
    has_child_tasks: bool | None = None     # True=仅叶节点(无结构子),False=仅内部节点(有结构子)


@dataclass
class TaskOpResult:
    """facade 级返回。"""

    task_id: str
    success: bool
    error: str | None = None
    run_id: int | None = None
    extend_props: dict | None = None


@dataclass
class NodeOpResult:
    """节点级写返回。"""

    task_id: str
    node_id: str
    success: bool
    prev_status: Status | None = None
    new_status: Status | None = None
    error: str | None = None


@dataclass
class TaskCallbackData:
    """回投数据协议(对齐执行模块文档)。

    单字段 ``data: Any``:执行实体 PUSH 回投的载荷。约定为 ``dict``,内含回框架路由键与结果:
    ``loop_task_id``("task_id::node_id")、``workflow_type``、``workflow_id``/``instance_id``、
    ``workflow_source``/``workflow_instance_id``(落 ``task_callback`` 时的 NOT NULL 来源)、
    ``result``({``success``/``data``/``gaps``/``exec_error``/``fail_detail``/``_ext_info``})。
    非回投构造路径请经各 ``translator`` 组装该 dict。

    消费侧约定:``data`` 为 ``dict`` 时从中解析回调记录字段并落 ``task_callback`` 表
    (见 ``TaskLoopCallback``);非 ``dict`` 时仅作原始透传,不解析、不落库。
    """

    data: Any


@dataclass
class PlanResult:
    """规划产物(对齐 plan 返回契约)。四象限驱动编排:　

    - ``children`` 非空 → gap 未闭,有可执行子任务:add_task_nodes + dispatch;　
    - ``children`` 空 ∧ ``has_gap``=False → gap 已闭(验收通过):节点 DONE 上行 / 根 gap 闭→图终态;　
    - ``children`` 空 ∧ ``has_gap``=True → 有 gap 但拆不出子(无规划能力):深度闸门判断(<MAX 升 BBS / ≥MAX HUNG)。
    """

    children: list["TaskNode"] = field(default_factory=list)
    has_gap: bool = False
    gap_detail: str = ""                # gap 描述(空+has_gap=True 时说明为何拆不出;has_gap=False 时可为 "done")
    acceptance_result: AcceptanceResult | None = None  # owner bot plan 自评(对齐 common_task 协议 {verdict,done_items:[{id,passed,summary}],gap_items});gap 闭翻 DONE 时直接用作父自身验收结果,空则回退合成
    # REQ-3 PLAN 轨迹溯源(additive optional 字段,默认 None → 完全向后兼容;现有构造不影响)
    strategy_name: str | None = None         # "workflow" | "gap_based" | None — 命中策略名
    prompt_digest: str | None = None         # SHA-256(prompt + response[:500]);workflow/未命中 → None
    raw_response_digest: str | None = None   # SHA-256(response[:500]);workflow/未命中 → None
    planned_children: list[str] | None = None  # 去重后子 node_id 列表(TaskPlanner.plan 回填)
