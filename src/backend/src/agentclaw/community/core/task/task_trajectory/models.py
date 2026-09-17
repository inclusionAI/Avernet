"""任务轨迹采集与根因分析领域模型(REQ-1 / REQ-9)。

纯 dataclass / enum 内存态领域对象 —— **transport/DB-agnostic**:
不依赖 SQLAlchemy / FastAPI / 任何外部框架,不读不写 ``task_action_log``。
``action_input`` / ``analysis`` / ``error_type`` / ``error_msg`` 等使用 ``... | None``
均为有意合法域态(``None`` = 未计算 / 成功 / 无输入);``gmt_*`` 沿用本模块 ``int`` 毫秒
时间戳约定(对齐 ``NodeActionEvent.ts`` / ``RuntimeInfo.start_time``),storage 层在做
``timestamp`` ↔ ``int`` 转换。``TrajectoryActionType`` 是**独立枚举**(新增 ``submit``、
不改 ``NodeAction``),二者不互为父/子集。

权威源:``src/backend/specs/2026-09-16-task-trajectory-collection-and-analysis/spec.md``
(REQ-1 模型、REQ-9 ``TrajectoryAnalysis`` 字段、REQ-2 ``DispatchRationale`` 形态)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agentclaw.community.core.task.domain.models import Status


class TrajectoryActionType(StrEnum):
    """轨迹事件动作类型(独立于 ``NodeAction``;新增 ``submit``,不改 ``NodeAction``)。

    与 ``NodeAction`` 的关系:二者均覆盖生命周期闸门动作,但 ``TrajectoryActionType`` 额外含
    ``submit``(提交段,见 REQ-6),且为**独立枚举类**(不继承、互不为父/子集),以满足
    “轨迹动作类型集合新增 ``submit``、不改 ``NodeAction`` 枚举”的约束。
    """

    SUBMIT = "submit"           # 任务提交(轨迹独有,不在 NodeAction)
    PLAN = "plan"               # 规划
    DISPATCH = "dispatch"       # 搜推派发
    EXECUTE = "execute"         # 实际执行
    VERIFY = "verify"           # 验收
    RESET = "reset"             # harness 复位重投
    TRANSITION = "transition"   # 框架直驱翻态 / 终态翻转


class ReasonCatalog(StrEnum):
    """根因分类目录(覆盖 §概述既有失败信号 + REQ-5 ``exec_error_origin`` 分类 + 派发侧 JOIN 丢因)。

    前 10 项为通用失败分类(REQ-9 ``failure_reason`` 派生依据);后 5 项为派发侧 JOIN 滤除
    /无候选场景,随 DISPATCH 事件的 ``DispatchRationale.join_dropped`` 写入事件行 ``ext_info``
    (非 ``TrajectoryEvent.error_type`` 取值)。
    """

    # 通用失败分类
    EXECUTION_TIMEOUT = "execution_timeout"
    UNDERLYING_INTERFACE_ERROR = "underlying_interface_error"
    DISPATCH_STUCK = "dispatch_stuck"
    HUNG = "hung"
    PLAN_FAILURE = "plan_failure"
    ACCEPTANCE_FAILED = "acceptance_failed"
    PARSE_ERROR = "parse_error"
    TRANSPORT_ERROR = "transport_error"
    TERMINAL_INVALID = "terminal_invalid"
    UNCLASSIFIED = "unclassified"
    # 派发侧 JOIN 丢因 / 无候选(置于 DispatchRationale.join_dropped / DISPATCH ext_info)
    JOIN_DROPPED = "join_dropped"
    NO_CANDIDATES = "no_candidates"
    SCORE_BELOW_THRESHOLD = "score_below_threshold"
    CLAIM_MODE_OFF = "claim_mode_off"
    CATALOG_MISS = "catalog_miss"


@dataclass
class TrajectoryEvent:
    """单条轨迹事件(FLAT 投影行;无嵌套 ``payload`` / ``rationale`` / ``phase``)。

    定型列即领域字段;附加素材(``DispatchRationale`` / RESET 计量 / SUBMIT 来源等)由采集层
    写入事件行 ``ext_info`` 自由 JSON 列(领域对象不映射,analyzer 按需读)。
    ``gmt_create`` = 事件发射时间(timeline 排序依据);``gmt_modified`` 仅在分析回填 ``analysis``
    时更新(未回填时 == ``gmt_create``)。``analysis`` 为内嵌 ``TrajectoryAnalysis`` JSON 字符串,
    发射时为 ``None``,分析完成后统一回填(REC-9)。``action_input`` **不截断**(原文落库)。
    """

    task_id: str
    node_id: str
    action_type: TrajectoryActionType
    action_result: str                       # 动作结果(success|hit_single|miss|failed|sla_timeout|...)
    attempt: int                             # harness 重试序号快照
    gmt_create: int                          # 事件发射时间(ms epoch)
    gmt_modified: int                          # 分析回填时更新(未回填 == gmt_create)
    action_input: str | None = None          # submit=task_spec_digest / plan=prompt_digest / execute|verify=request_input / reset|transition=None
    status_from: Status | None = None        # 动作前节点状态(未翻态时 None)
    status_to: Status | None = None          # 动作后节点状态(未翻态时 None)
    error_type: ReasonCatalog | None = None   # 仅出错时填(成功为 None)
    error_msg: str | None = None              # 截断错误消息(成功为 None)
    analysis: str | None = None              # 内嵌 TrajectoryAnalysis JSON(发射时 None)


@dataclass
class TaskTrajectory:
    """任务轨迹(仅时间线;无 ``phases`` / 无 ``graph_snapshot``)。

    组装层(REQ-8)按 ``gmt_create`` 升序读事件行拼装;``analysis`` 组装产出时为 ``None``,
    分析完成后回填。``gmt_create`` = 组装产出时间,``gmt_modified`` = 回填 ``analysis`` 时
    更新(未分析时 == ``gmt_create``)。
    """

    task_id: str
    gmt_create: int
    gmt_modified: int
    timeline: list[TrajectoryEvent] = field(default_factory=list)
    analysis: str | None = None


@dataclass
class TrajectoryAnalysis:
    """轨迹分析结果(扁平原因字符串;无 verdict 对象、不含事件列表——避免 ``json.dumps`` 递归)。

    ``analysis_type`` ∈ {``llm``, ``tc_bot``, ``rule``}(LLM / tc_bot / 确定性规则归因);
    ``analysis_executor`` = 执行者自身 id(``llm``=大模型名 / ``tc_bot``=bot_id /
    ``rule``=``rule_engine``);``boost_reason`` / ``failure_reason`` 均为扁平字符串,
    由分析执行者填充(REQ-9)。
    """

    analysis_type: str                        # "llm" | "tc_bot" | "rule"
    analysis_executor: str                    # 执行者自身 id
    analysis_input: str                       # 喂给分析器的结构化输入摘要(事件 + ext_info 概要)
    analysis_output: str                      # 结论汇总文本(boost/failure 综合呈现)
    gmt_create: int                           # 分析产出时间(ms epoch)
    boost_reason: str | None = None           # 派发“为何选该执行者”(末条 DISPATCH 派生)
    failure_reason: str | None = None         # 失败根因(末条 terminal 事件派生)


@dataclass
class DispatchCandidate:
    """派发候选(``DispatchRationale.candidates`` 元素)。"""

    bot_id: str
    recommend_score: float
    short_profile: str


@dataclass
class JoinDropped:
    """JOIN 滤除条目(``DispatchRationale.join_dropped`` 元素)。

    ``reason`` 取值见 REQ-7:``claim_mode_off | catalog_miss | score_below_threshold |
    claim_filter_disabled``。为字符串(不完全等于 ``ReasonCatalog`` —— ``claim_filter_disabled``
    不在 ``ReasonCatalog`` 中)。
    """

    bot_id: str
    reason: str


@dataclass
class DispatchRationale:
    """派发“为何”富化(REQ-2),随 DISPATCH 轨迹事件写入事件行 ``ext_info`` 列(自由 JSON)。

    采集层在 DIRECT / SEARCH 策略 ``apply`` 时填充,引擎 DISPATCH 闸门旁路发射轨迹事件时
    整体写入 ``ext_info``(领域对象不映射 ``ext_info``);analyzer 经 ``ext_info_lookup`` 按需
    读取并派生 ``boost_reason``。组装全程 ``try/except`` —— 任一子字段缺失不影响 DISPATCH
    事件正常发射(仅对应字段缺失 + DEBUG 日志)。
    """

    strategy_name: str                           # direct | search | replay | bbs
    decision_mode: str                           # direct | rule | skill | replay | bbs
    join_filter_applied: bool
    candidates: list[DispatchCandidate] = field(default_factory=list)
    prefetch_tokens: list[str] = field(default_factory=list)
    join_dropped: list[JoinDropped] = field(default_factory=list)
    skill_prompt_digest: str | None = None       # search-skill 模式:SHA-256(prompt)+截断响应 digest
    skill_response_digest: str | None = None
