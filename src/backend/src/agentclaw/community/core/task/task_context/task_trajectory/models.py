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
from typing import Any

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
    RELAY = "relay"             # 分布式接力(orchestration_mode==relay):bootstrap/report/plan/dispatch/bbs/resume


class AnalysisType(StrEnum):
    """轨迹分析执行者类型(REQ-9 多执行者框架;决策 #10/11)。

    首期仅 ``TC_BOT`` 经 ``GET /trajectory?do_analysis=true`` 触发(DI 配置注入 bot_id);
    ``RULE`` 为确定性规则归因(纯函数,首期不自动触发,单测覆盖);``LLM`` 为大模型执行者
    (首期 stub,决策 #11)。``AnalysisType`` 是 ``StrEnum`` —— 成员与其 ``.value`` 字符串
    比较相等(``AnalysisType.TC_BOT == "tc_bot"``),保证 P0 以 ``str`` 形态构造
    ``TrajectoryAnalysis`` 的旧代码向后兼容(M1 收紧:``TrajectoryAnalysis.analysis_type``
    由 ``str`` 提升为 ``AnalysisType``;dataclass 不在运行期强制,旧 ``str`` 传参仍可用)。
    """

    LLM = "llm"
    TC_BOT = "tc_bot"
    RULE = "rule"


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
    RELAY = "relay"             # 接力失败(派发失败 / turn 失效 / resume 耗尽等;非 analyzer failure_reason 派生点)
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
    写入事件行 ``ext_info`` 自由 JSON 列(领域对象不整体映射,仅定向投影 ``holder_id`` 与
    ``search_probe``(派发搜推三问采样)供展示,analyzer 仍按需读取完整 JSON)。
    ``gmt_create`` = 事件发射时间(timeline 排序依据);``gmt_modified`` 仅在分析回填 ``analysis``
    时更新(未回填时 == ``gmt_create``)。``analysis`` 为内嵌 ``TrajectoryAnalysis`` JSON 字符串,
    发射时为 ``None``,分析完成后统一回填(REC-9)。``action_input`` **不截断**(原文落库)。

    ``output`` = **读时富化**字段(不落库):子任务**(task_id+node_id)当前产出**
    (``node.run_info.output``,经 ``task_execution_graph`` 查询接口获取),由 service
    在 ``get_trajectory`` 返回前挂到该节点在 timeline 中的**最后一条**事件上;库行
    无此列,组装的原始值为 ``None``,富化失败/未接线也保持 ``None``(缺字段=无信号)。

    ``search_probe`` = **读时定向投影**字段(不落库;与 ``holder_id`` 同款白名单
    投影,是 REQ-1 "ext_info 不整体入领域" 边界的定向扩展而非破坏):派发搜推三问
    (关键词/返回结果/最终选择)的可展示摘要,由 assembler 从本事件行 ``ext_info``
    投影——DISPATCH 事件取 ``_dispatch_rationale``(decision_mode/tokens/keywords/
    raw_item_count/failed_keywords/candidates/selected/dropped/rule_selection_note/
    skill_response_excerpt/miss_reason),relay ``action_result="search"`` 事件取
    顶层 ``search_sampling``+``candidates``。无素材的事件保持 ``None``(缺字段=无信号)。
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
    boost_reason: str | None = None           # 本次动作推进原因(成功推进时可填)
    holder_id: str | None = None              # Relay 当前动作执行人(ext_info 定向投影)
    analysis: str | None = None              # 内嵌 TrajectoryAnalysis JSON(发射时 None)
    output: dict[str, Any] | None = None      # 读时富化:节点当前产出(仅该节点最后一条事件;不落库)
    session_msgs: list[dict[str, Any]] | None = None  # 读时富化:子任务会话消息(同上;源自末位事件 ext_info.session_msgs,展示/DTO 用,不落库)
    search_probe: dict[str, Any] | None = None  # 读时定向投影:派发搜推三问采样摘要(源自本事件 ext_info;不落库,详见类 docstring)


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

    analysis_type: AnalysisType               # "llm" | "tc_bot" | "rule" (M1 收紧: StrEnum)
    analysis_executor: str                    # 执行者自身 id
    analysis_input: str                       # 喂给分析器的结构化输入摘要(事件 + ext_info 概要)
    analysis_output: str                      # 结论汇总文本(boost/failure 综合呈现)
    gmt_create: int                           # 分析产出时间(ms epoch)
    boost_reason: str | None = None           # 派发“为何选该执行者”(末条 DISPATCH 派生)
    failure_reason: str | None = None         # 失败根因(末条 terminal 事件派生)
    final_status: str | None = None           # 大模型判定:任务是否最终执行成功(SUCCESS/FAILED/HUNG/RUNNING/UNKNOWN)
    error_category: str | None = None         # 大模型判定:失败错误类型(execution_error/dispatch_error/plan_error/interface_error/timeout_error/acceptance_error/unknown;成功为 None)
    timeline_version: str | None = None       # 分析时 timeline 指纹(sha256:事件数+逐条 id:gmt_create 正序;由 service backfill 前盖戳,do_analysis 依它做增量幂等)


@dataclass
class DispatchCandidate:
    """派发候选(``DispatchRationale.candidates`` 元素)。"""

    bot_id: str
    recommend_score: float
    short_profile: str
    # 搜推候选投影扩展(全部可选):``bot_name``/``owner_name`` 取自 catalog 命中 item;
    # ``reasons`` 为 ``recommend.reasons`` 截断投影(每条 ≤100 字符,最多前 5 条)。
    bot_name: str | None = None
    owner_name: str | None = None
    reasons: list[str] | None = None


@dataclass
class JoinDropped:
    """JOIN 滤除条目(``DispatchRationale.join_dropped`` 元素)。

    ``reason`` 取值见 REQ-7:``claim_mode_off | catalog_miss | score_below_threshold |
    claim_filter_disabled``。为字符串(不完全等于 ``ReasonCatalog`` —— ``claim_filter_disabled``
    不在 ``ReasonCatalog`` 中)。

    **跨字段不变式(必须与 ``DispatchRationale.join_filter_applied`` 一起解读)**:
    当 ``join_filter_applied is False`` 时(JOIN 灰度开关关 / bcn 缺 / 名册异常 fail-open),
    ``join_dropped`` 中的条目**不是实际发生的丢弃**,而是排障**visibility flag** —— 描述
    "若 filter 开启本应被丢"的 bot,该 bot 实际**已透传** ``SearchResult`` 未被丢弃(克隆
    fail-open 链路保持原行为)。此时唯一出现的 ``reason`` 是 ``claim_filter_disabled``。
    当 ``join_filter_applied is True`` 时,``join_dropped`` 条目是**真实丢弃**(``claim_mode_off``
    / ``catalog_miss`` / ``score_below_threshold``),``SearchResult.unauthorized_bots`` 同步记录
    (后者 ``reason`` 保留 ``claim_mode_off`` 兼容 dashboard 契约,与本字段微分类不冲突)。

    误读示例:仅看 ``join_dropped[0].reason == "claim_filter_disabled"`` 推断 "bot X 因 JOIN
    filter 关闭被丢" 是错误的 —— 实际 X **仍在** ``SearchResult.bot_id`` /
    ``.group_formation.bot_ids``,未被丢弃。
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
    # **跨字段不变式**:解读 ``join_dropped`` 必须同时看 ``join_filter_applied``。当
    # ``join_filter_applied is False`` 时,``join_dropped`` 条目为排障 visibility flag,
    # 描述"若 filter 开启本应被丢"的 bot(reason 唯一取 ``claim_filter_disabled``),
    # 实际**未丢弃**(克隆 fail-open 透传 ``SearchResult``)。当
    # ``join_filter_applied is True`` 时,``join_dropped`` 条目是真实丢弃(``claim_mode_off`` /
    # ``catalog_miss`` / ``score_below_threshold``)。详见 ``JoinDropped`` 文档。
    join_dropped: list[JoinDropped] = field(default_factory=list)
    skill_prompt_digest: str | None = None       # search-skill 模式:SHA-256(prompt)+截断响应 digest
    skill_response_digest: str | None = None
    # 搜推采样(三问:关键词/返回结果/最终选择)。``skill_prompt`` 刻意只存 digest(体量大,
    # 候选目录另有 ``candidates`` 结构化记录);``skill_response_excerpt`` 是 owner bot 决策
    # 回包原文(≤2000 字符,"最终选择"的直接证据);``miss_reason`` 为 MISS 原因(此前只在
    # ``miss_events`` 可见,轨迹侧补齐)。
    skill_response_excerpt: str | None = None
    miss_reason: str | None = None
    search_sampling: SearchSampling | None = None


@dataclass
class SearchKeywordHit:
    """单关键词搜回事实(轨迹侧投影,源自召回层 ``candidate_search.KeywordHit``)。

    ``item_count`` 为该关键词原始命中条数(候选去重前);``bot_ids`` 为该关键词命中的
    归一候选 identity(原序,跨关键词不去重);``failed`` 标记该关键词的 discover 调用
    是否异常(异常时 ``bot_ids`` 为空、``item_count`` 为 0,关键词同步计入
    ``SearchSampling.failed_keywords``)。
    """

    keyword: str
    item_count: int
    bot_ids: list[str]
    failed: bool


@dataclass
class SearchSampling:
    """DISPATCH 事件的搜推采样对象(``DispatchRationale.search_sampling``)。

    覆盖"搜推三问":①关键词(``keywords``/``failed_keywords``/``raw_item_count``);
    ②返回结果(``keywords[].bot_ids`` + ``DispatchRationale.candidates`` 全量投影);
    ③最终选择(rule 模式的 ``selected_bot_ids``/``dropped_bot_ids`` +
    ``rule_selection_note``;skill 模式的选择自由度在 ``skill_response_excerpt`` 回包
    原文里,不做机械推导)。relay ``/search`` 路径的``ext_info["search_sampling"]``
    与本对象同形(无 rationale 载体的场景单独携带 ``candidates`` 键)。
    """

    keywords: list[SearchKeywordHit] = field(default_factory=list)
    raw_item_count: int = 0
    failed_keywords: list[str] = field(default_factory=list)
    # rule 模式选择证据(框架机械截断;skill 模式置空):
    selected_bot_ids: list[str] = field(default_factory=list)
    dropped_bot_ids: list[str] = field(default_factory=list)
    # "hit_single_takes_first" | "hit_multi_capped_at_3"
    rule_selection_note: str | None = None
