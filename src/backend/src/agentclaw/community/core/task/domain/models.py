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
    VERIFY = "verify"           # 验收结论;payload: verdict/acceptances_metric/gaps
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
class Metadata:
    task_id: str
    title: str
    instruction: str               # 核心执行指令(Prompt/提示词)

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "title": self.title, "instruction": self.instruction}


@dataclass
class Context:
    background: str
    extend_props: dict[str, Any] = field(default_factory=dict)  # 上下文扩展属性(非结构化补充)

    def to_dict(self) -> dict[str, Any]:
        return {"background": self.background, "extend_props": dict(self.extend_props)}


@dataclass
class AcceptanceCriteria:
    id: str
    description: str               # 验收标准的具体描述(无 type 字段)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "description": self.description}


@dataclass
class Goal:
    objective: str
    acceptances: list[AcceptanceCriteria]

    def to_dict(self) -> dict[str, Any]:
        return {"objective": self.objective, "acceptances": [a.to_dict() for a in self.acceptances]}


@dataclass
class TaskSpec:
    metadata: Metadata
    context: Context
    goal: Goal                     # 无 SLA

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "context": self.context.to_dict(),
            "goal": self.goal.to_dict(),
        }


@dataclass
class TaskInfo:
    """对外 ``execute`` 入参。"""

    task_spec: TaskSpec
    source_type: str       # "bot" | "coop_group"
    owner_bot_id: str         # owning bot id
    owner_user_id: str = ""   # owning user id, kept separate from owner_bot_id
    execution_config: dict[str, Any] = field(default_factory=dict)  # 指定 bot/workflow yaml/MAX_DEPTH 等


# ===== 运行态(Runtime Graph)=====
@dataclass
class AcceptanceResult:
    """验收/审计结果(无 verifier 字段)。"""

    verdict: AcceptanceVerdict
    acceptances_metric: list[Any] = field(default_factory=list)  # 已满足的验收指标明细(新协议为指标对象数组,放宽为 Any)
    gaps: list[Any] = field(default_factory=list)  # 与期望目标的差距(驱动 plan 自算,非 plan 入参);新协议 FAIL 为对象数组,放宽为 Any


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
    output: dict[str, Any] = field(default_factory=dict)
    acceptance_result: AcceptanceResult | None = None
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
    def effective_status(self) -> "Status":
        """图级有效态(乙' c+R2 只读派生根态):有根节点时以根态为准,使"图状态与根节点状态保持一致"
        落在观测口径;无根(未初始化)回落存储的图级 ``status``。

        纯只读派生,不改并发主线——图级 ``status`` 仍由编排核 ``update_task_graph_info`` 显式写
        (终态收口 / loop_exhausted / 外部镜像);控制流(``_is_graph_terminal`` 等)继续读 ``status``,
        本属性供看板/持久化等"以根态为准"的观测口径消费。与 ``_persist_locked`` 既有 root 派生
        (runtime_status)完全等价,是其单源化的命名口径。"""
        root = next((n for n in self.tasks if n.node_id == self.task_id), None)
        return root.status if root is not None else self.status



@dataclass
class TaskSummary:
    """任务摘要(列表视图轻量投影;非完整图)。``list_task_summaries`` 返回项。"""

    task_id: str
    run_id: int
    status: Status
    title: str = ""              # 根节点 task_spec.metadata.title
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
    acceptance_result: AcceptanceResult | None = None        # 验收驱动终态翻转(PASS→DONE/FAIL+gaps→DONE)
    exec_error: str | None = None                            # 执行报错信号(非验收;→ on_harness 重投,)
    extend_props_patch: dict[str, Any] | None = None         # miss_events / hung_reason(stuck) / harness_retries / 崩溃栈


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
    acceptance_verdicts: list[dict[str, Any]] = field(default_factory=list)  # 逐条验收结论:[{ac_id,passed,reason}];owner bot plan 一并吐出,供结构父 gap 闭翻 DONE 时构造父自身 acceptance_result
