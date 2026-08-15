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
    """任务/节点执行状态(6 态,含 PLANNING)。"""

    PENDING = "PENDING"      # 待处理
    PLANNING = "PLANNING"    # 规划中(被分解委托子执行,显式委托态)
    RUNNING = "RUNNING"      # 运行中
    DONE = "DONE"            # 已成功完成
    FAILED = "FAILED"        # 执行失败(验收未通过,带 gaps)
    HUNG = "HUNG"            # 已挂起/暂停(仅 stuck:迭代达上限执行不下去,需人介入)


class AcceptanceVerdict(StrEnum):
    """验收结论。"""

    PASS = "PASS"
    FAIL = "FAIL"


class RelationType(StrEnum):
    """节点间关系类型。"""

    DEPENDENCY = "DEPENDENCY"   # 分解树边(承载结构归属,单入)


# ===== 规格面(Task Specification)=====
@dataclass
class Metadata:
    task_id: str
    title: str
    instruction: str               # 核心执行指令(Prompt/提示词)


@dataclass
class Context:
    background: str
    extend_props: dict[str, Any] = field(default_factory=dict)  # 上下文扩展属性(非结构化补充)


@dataclass
class AcceptanceCriteria:
    id: str
    description: str               # 验收标准的具体描述(无 type 字段)


@dataclass
class Goal:
    objective: str
    acceptances: list[AcceptanceCriteria]


@dataclass
class TaskSpec:
    metadata: Metadata
    context: Context
    goal: Goal                     # 无 SLA


@dataclass
class TaskInfo:
    """对外 ``execute`` 入参。"""

    task_spec: TaskSpec
    source_channel_type: str       # "bot" | "coop_group"
    source_channel_id: str         # bot_id / 协作群 id
    execution_config: dict[str, Any] = field(default_factory=dict)  # 指定 bot/workflow yaml/MAX_DEPTH 等


# ===== 运行态(Runtime Graph)=====
@dataclass
class AcceptanceResult:
    """验收/审计结果(无 verifier 字段)。"""

    verdict: AcceptanceVerdict
    acceptances_metric: list[str] = field(default_factory=list)  # 已满足的验收指标明细
    gaps: list[str] = field(default_factory=list)                # 与期望目标的差距(驱动 plan 自算,非 plan 入参)


@dataclass
class RuntimeInfo:
    """节点运行时实时执行信息(所有 None 均合法域态)。"""

    run_mode: str | None = None              # "single_bot"/"coop_group"/"bbs";无 collab_mode
    assignee: str | None = None              # 执行者(bot_id / group_id)
    start_time: float | None = None
    end_time: float | None = None
    output: dict[str, Any] = field(default_factory=dict)
    acceptance_result: AcceptanceResult | None = None
    extend_props: dict[str, Any] = field(default_factory=dict)  # miss_events/崩溃栈/超时/hung_reason(stuck)


@dataclass
class Relation:
    """分解树边(一等公民);承载结构归属,单入(每非根节点恰好 1 入边=结构父)。"""

    src_id: str                   # 结构父(分解源/被依赖)
    dst_id: str                   # 结构子(分解产物/依赖方)
    type: RelationType = RelationType.DEPENDENCY
    extend_props: dict[str, Any] = field(default_factory=dict)


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
    # 派生不持久: depth / child_tasks / parent_task(均从 relations 分解树派生)



@dataclass
class TaskSummary:
    """任务摘要(列表视图轻量投影;非完整图)。``list_task_summaries`` 返回项。"""

    task_id: str
    run_id: int
    status: Status
    title: str = ""              # 根节点 task_spec.metadata.title
    node_count: int = 0          # 图中节点总数
    loop_round: int = 0          # 图级轮次

# ===== 中间类型(patch/criteria/op_result/callback)=====
@dataclass
class TaskNodePatch:
    """节点级原子写(``update_task_node_info`` 入参)。

    终态翻转三选一(互斥):　
    ① ``acceptance_result`` 非空 → 验收驱动(RUNNING→DONE/FAILED):PASS→DONE / FAIL+gaps→FAILED;　
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
    output_patch: dict[str, Any] | None = None               # fold 到 run_info.output
    acceptance_result: AcceptanceResult | None = None        # 验收驱动终态翻转(PASS→DONE/FAIL+gaps→FAILED)
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
    """回投数据协议(对齐执行模块文档)。"""

    loop_task_id: str             # 关联回框架侧 (task_id, node_id)
    workflow_type: str            # "single_bot" | "bcn_coop_group" | "bbs" | ...
    workflow_id: int
    instance_id: int              # workflow 运行实例 id
    result: dict[str, Any]        # {"success": bool, "data": "..."} / {"fail_detail": "..."}


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
