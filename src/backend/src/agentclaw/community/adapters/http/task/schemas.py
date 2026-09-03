"""Task HTTP API schemas —— FastAPI 边界 DTO <-> core task domain models。

纯 pydantic.BaseModel 序列化,不含业务逻辑(Rule 22:HTTP adapter 只转协议)。
对齐 domain/models.py 的 TaskInfo/TaskCallbackData/TaskExecutionGraph/TaskOpResult/TaskNode 字段。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Success/error envelopes come from the unified /openapi/v1 contract
# (``openapi_v1.contracts.Envelope`` / ``ErrorEnvelope``); this module keeps only
# the task-specific request/response DTOs.


# ===== Request DTOs =====


class MetadataDTO(BaseModel):
    """任务元数据(标识 + 标题 + 核心指令)。"""

    task_id: str = Field(..., description="任务ID")
    title: str = Field("", description="任务标题")
    instruction: str = Field("", description="核心执行指令(Prompt)")


class ContextDTO(BaseModel):
    """任务执行上下文(背景 + 扩展属性)。"""

    background: str = Field("", description="任务背景信息")
    extend_props: dict[str, Any] = Field(
        default_factory=dict, description="上下文扩展属性"
    )


class AcceptanceCriteriaDTO(BaseModel):
    """单条验收标准(标识 + 描述)。"""

    id: str = Field(..., description="验收标准唯一标识")
    description: str = Field("", description="验收标准具体描述")


class GoalDTO(BaseModel):
    """任务目标(目标描述 + 验收标准列表)。"""

    objective: str = Field("", description="任务目标描述")
    acceptances: list[AcceptanceCriteriaDTO] = Field(
        default_factory=list, description="验收标准列表"
    )


class TaskSpecDTO(BaseModel):
    """任务规格(元数据 + 上下文 + 目标);response 面与 Request* 同构。"""

    metadata: MetadataDTO = Field(..., description="任务元数据(标识/标题/指令)")
    context: ContextDTO = Field(
        default_factory=ContextDTO, description="任务执行上下文"
    )
    goal: GoalDTO = Field(default_factory=GoalDTO, description="任务目标与验收标准")


class RequestMetadataDTO(BaseModel):
    """提交任务的元数据(标题 + 指令;task_id 服务端生成)。"""

    title: str = Field("", description="任务标题")
    instruction: str = Field("", description="核心执行指令(Prompt)")


class RequestAcceptanceDTO(BaseModel):
    """提交单条验收标准(标识 + 描述)。"""

    id: str = Field(..., description="验收标准唯一标识")
    acceptance: str = Field("", description="验收标准具体描述")


class RequestGoalDTO(BaseModel):
    """提交任务目标(目标描述 + 验收标准列表)。"""

    objective: str = Field("", description="任务目标描述")
    acceptances: list[RequestAcceptanceDTO] = Field(
        default_factory=list, description="验收标准列表"
    )


class RequestTaskSpecDTO(BaseModel):
    """提交任务规格(元数据 + 上下文 + 目标)。"""

    metadata: RequestMetadataDTO = Field(..., description="任务元数据(标题/指令)")
    context: ContextDTO = Field(
        default_factory=ContextDTO, description="任务执行上下文"
    )
    goal: RequestGoalDTO = Field(
        default_factory=GoalDTO, description="任务目标与验收标准"
    )


class ExecutionConfigDTO(BaseModel):
    """执行配置(task_type 必填;yaml/workflow_id 可选;其余键允许透传)。"""

    model_config = ConfigDict(extra="allow")
    task_type: Literal["yaml", "workflow", "dynamic", "static_plan", "bbs"] = Field(
        ..., description="任务类型"
    )
    yaml: str | dict[str, Any] | None = Field(None, description="yaml 内联或引用")
    workflow_id: str | None = Field(None, description="workflow id")
    panel_component_name: str | None = Field(
        None,
        description="state_machine 协作群 opening_message 使用的业务面板组件名",
    )


class TaskInfoRequestDTO(BaseModel):
    """POST .../collaboration/tasks/execute 请求体(对外扁平契约;task_id 服务端生成)。"""

    task_spec: RequestTaskSpecDTO = Field(
        ..., description="任务规格(元数据/上下文/目标)"
    )
    source_type: Literal["bot", "coop_group", "api"] = Field(
        "bot", description="触发渠道类型"
    )
    owner_user_id: str = Field(..., description="userId")
    owner_bot_id: str = Field(..., description="botId")
    execution_config: ExecutionConfigDTO = Field(
        default_factory=lambda: ExecutionConfigDTO(task_type="dynamic"),
        description="执行配置(task_type/yaml/workflow_id + 透传键)",
    )


class BbsClaimDTO(BaseModel):
    """POST /api/v1/collaboration/tasks/bbs/claim 请求体。"""

    task_id: str = Field(..., description="任务ID(BBS 接力根级 CAS 占有目标)")
    bot_id: str = Field(..., description="发起占有的 bot id")


class BbsAttachDTO(BaseModel):
    """POST /api/v1/collaboration/tasks/bbs/attach 请求体(BBS 接力步④:挂 scoped bbs 子节点 + start)。"""

    task_id: str = Field(..., description="任务ID")
    parent_node_id: str = Field(..., description="父节点ID(挂入分解树的 parent)")
    task_spec: TaskSpecDTO = Field(..., description="scoped 子节点任务规格")
    bot_id: str = Field(..., description="发起挂接的 bot id(须为当前 bbs_owner)")


class BbsResultDTO(BaseModel):
    """POST /api/v1/collaboration/tasks/bbs/result 请求体(BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim)。

    收口不由 bot 声明:框架经 owner 复核根 gap 满足后自行收口(``on_bbs_report``→``_on_pass_collect``→
    ``_maybe_finish_graph``),故无 ``root_verified`` 字段。
    """

    task_id: str = Field(..., description="任务ID")
    node_id: str = Field(..., description="scoped 子节点ID(attach 返回的 bbs- 节点)")
    bot_id: str = Field(..., description="回投 bot id(须为当前 bbs_owner)")
    acceptance_result: AcceptanceResultDTO | None = Field(
        None, description="验收结论(PASS/FAIL)"
    )
    output_patch: dict[str, Any] | None = Field(
        None, description="checkpoint fold 增量输出"
    )
    exec_error: str | None = Field(None, description="执行报错(fold 进节点)")


class TaskNodeUpdateDTO(BaseModel):
    """POST /api/v1/collaboration/tasks/nodes/update 请求体(内部节点写口:直接更新节点 run_info)。

    内部调用方/测试用直驱写口,透传 ``TaskNodePatch`` 经 ``ExecutionEngine.on_report`` 落库并触发翻态/
    验收/收敛传播(与回投同一入口,故 status 直驱仍会触发引擎收敛旁路)。
    终态翻转三选一(互斥,与 ``TaskNodePatch`` 对齐):``acceptance_result`` 验收驱动 / ``exec_error`` 执行报错
    (→ on_harness 重投)/ ``status`` 框架直驱;三者全空仅 fold 非状态字段。
    """

    task_id: str = Field(..., description="任务ID")
    node_id: str = Field(..., description="节点ID")
    status: str | None = Field(None, description="框架直驱状态(HUNG/DONE/SUCCESS/PENDING/RUNNING 等)")
    run_mode: str | None = Field(None, description="执行模态(single_bot / bbs 等)")
    assignee: str | None = Field(None, description="承接者(bot_id 或 group_id)")
    output_patch: dict[str, Any] | None = Field(None, description="增量输出(fold 进 run_info.output)")
    acceptance_result: AcceptanceResultDTO | None = Field(
        None, description="验收结论(PASS→DONE / FAIL+gaps→FAILED)"
    )
    exec_error: str | None = Field(None, description="执行报错信号(非验收;→ on_harness 重投)")
    extend_props_patch: dict[str, Any] | None = Field(
        None, description="增量扩展属性(miss_events / hung_reason / harness_retries 等)"
    )


class TaskCallbackDataDTO(BaseModel):
    """POST /api/v1/collaboration/tasks/callback/report 请求体(执行实体回投)。"""

    loop_task_id: str = Field(..., description="回投标识 f'{task_id}::{node_id}'")
    workflow_type: str = Field(
        "single_bot", description="执行模态 single_bot / bcn_coop_group"
    )
    workflow_id: int = Field(0, description="workflow id(占位)")
    instance_id: int = Field(0, description="instance id(占位)")
    result: dict[str, Any] = Field(
        default_factory=dict,
        description="回投结果 {success: bool, data?: any, fail_detail?: str}",
    )


# ===== Response DTOs =====


class TaskOpResultDTO(BaseModel):
    """任务操作结果(execute 提交 / op 级动作返回)。"""

    task_id: str = Field(..., description="任务ID(服务端生成)")
    success: bool = Field(..., description="操作是否成功")
    run_id: int = Field(0, description="图运行实例ID")
    message: str | None = Field(
        None, description="失败原因(success=false 时透出 error,便于排查)"
    )
    extend_props: dict[str, Any] = Field(
        default_factory=dict, description="操作结果扩展属性"
    )


class AcceptanceResultDTO(BaseModel):
    """验收结论(DONE/FAILED + 通过项与缺口)。"""

    verdict: str = Field(..., description="DONE / FAILED")
    acceptances_metric: list[Any] = Field(
        default_factory=list, description="通过的验收项明细列表(新协议为对象数组,[{项:结论}])"
    )
    gaps: list[Any] = Field(
        default_factory=list, description="未通过的验收项明细/差距(对象数组 [{项:原因}] 或字符串数组)"
    )


class NodeActionEventDTO(BaseModel):
    """节点动作级历史快照(诊断用;默认不序列化,include_action_log=true 时返回)。"""

    seq: int = Field(..., description="动作序号(同节点内单调递增)")
    ts: int = Field(..., description="动作发生时间戳(毫秒)")
    action: str = Field(
        ..., description="动作类型(PLAN/DISPATCH/EXECUTE/VERIFY/RESET/TRANSITION)"
    )
    loop_round: int = Field(0, description="所属 loop 轮次")
    attempt: int = Field(0, description="本动作的重试次数")
    status_from: str | None = Field(None, description="动作前的节点状态")
    status_to: str | None = Field(None, description="动作后的节点状态")
    payload: dict[str, Any] = Field(
        default_factory=dict, description="动作 payload(全量)"
    )


class RuntimeInfoDTO(BaseModel):
    """节点运行时信息(执行态 + 输出 + 验收 + 动作历史)。"""

    run_mode: str | None = Field(None, description="运行模式(single_bot / bbs 等)")
    assignee: str | None = Field(None, description="当前承接节点执行的 bot id")
    start_time: int | None = Field(None, description="执行开始时间戳(毫秒)")
    end_time: int | None = Field(None, description="执行结束时间戳(毫秒)")
    output: Any = Field(
        default_factory=dict,
        description="节点输出(checkpoint fold);adapter 路径以 output key 落 run_info.output,DTO 层展平为标量(去除两层 output 嵌套)",
    )
    acceptance_result: AcceptanceResultDTO | None = Field(None, description="验收结论")
    extend_props: dict[str, Any] = Field(
        default_factory=dict, description="运行时扩展属性"
    )
    action_log: list[NodeActionEventDTO] = Field(
        default_factory=list, description="节点动作历史(include_action_log=true 时填充)"
    )


class TaskNodeDTO(BaseModel):
    """分解树中的单个任务节点(规格 + 运行时信息)。"""

    node_id: str = Field(..., description="节点ID(分解树内唯一)")
    task_id: str = Field(..., description="所属任务ID")
    status: str = Field(
        ...,
        description="节点状态(product 态:DEFINED/EXECUTING/REVIEWING/DONE/SUCCESS/FAILED/CANCELLED)",
    )
    task_spec: TaskSpecDTO = Field(..., description="节点任务规格")
    run_info: RuntimeInfoDTO = Field(
        default_factory=RuntimeInfoDTO, description="节点运行时信息"
    )


class TaskInfoRecordDTO(BaseModel):
    """GET .../collaboration/tasks/list 返回的持久化任务记录。"""

    id: int = Field(..., description="持久化记录自增主键")
    task_id: str = Field(..., description="任务ID")
    source_type: str = Field(..., description="触发渠道类型(bot/coop_group/api)")
    owner_user_id: str = Field(..., description="归属 userId")
    owner_user_name: str | None = Field(None, description="归属用户名称")
    owner_bot_id: str = Field(..., description="归属 botId")
    owner_bot_name: str | None = Field(None, description="归属 Bot 名称")
    execution_config: dict[str, Any] | None = Field(
        None, description="执行配置(task_type/yaml/workflow_id + 透传键)"
    )
    task_spec: dict[str, Any] = Field(..., description="任务规格(元数据/上下文/目标)")
    status: str = Field(..., description="任务状态(product 态)")
    gmt_create: datetime | None = Field(None, description="记录创建时间")
    gmt_modified: datetime | None = Field(None, description="记录最后修改时间")


class BbsTaskItemDTO(BaseModel):
    """GET /api/v1/collaboration/tasks/bbs/list 返回的单条 BBS 接力任务概览。

    忠实映射给定 SQL 的列别名(task_node_run_info r ⋈ task_node n,再补 task_info.owner_bot_id)。
    title/goal/acceptances 由 adapter 二次解析自 task_spec;assignee_name 解析自 extend_props;
    publisher=task_info.owner_bot_id(按 task_id 补,缺失 → None)。status 取 task_node 原始运行态。
    """

    task_id: str = Field(..., description="任务ID(r.task_id)")
    node_id: str = Field(..., description="节点ID(r.node_id)")
    run_mode: str | None = Field(None, description="执行模态(r.run_mode;bbs)")
    retry: int = Field(0, description="重试序号(r.retry;当前恒 0)")
    assignee_id: str | None = Field(None, description="承接者ID(r.assignee → assignee_id)")
    status: str | None = Field(None, description="节点运行态(n.status 原值,如 RUNNING/DONE/SUCCESS/…)")
    acceptance_result: dict[str, Any] | None = Field(None, description="验收结论(r.acceptance_result)")
    extend_props: dict[str, Any] | None = Field(None, description="运行扩展属性(r.extend_props)")
    relay_create_time: datetime | None = Field(None, description="节点建表时间(n.gmt_create)")
    relay_begin_time: datetime | None = Field(None, description="run 建表时间(r.gmt_create)")
    relay_end_time: datetime | None = Field(None, description="run 改表时间(r.gmt_modified)")
    task_spec: dict[str, Any] = Field(..., description="节点任务规格(n.task_spec 原始 dict)")
    title: str | None = Field(None, description="解析自 task_spec.metadata.title")
    goal: str | None = Field(None, description="解析自 task_spec.goal.objective")
    acceptances: list[Any] | None = Field(None, description="解析自 task_spec.goal.acceptances")
    assignee_name: str | None = Field(None, description="解析自 extend_props.assignee_name")
    publisher: str | None = Field(None, description="发布方 botId(task_info.owner_bot_id)")
    publisher_name: str | None = Field(
        None, description="发布方 bot 名称(由 publisher bot_id 批量查 BotService;缺失/降级 → None)"
    )


class TaskRelationDTO(BaseModel):
    """分解树边(一等公民);承载结构归属,单入(每非根节点恰好 1 入边=结构父)。"""

    src_id: str = Field(..., description="结构父(分解源/被依赖)")
    dst_id: str = Field(..., description="结构子(分解产物/依赖方)")
    type: str = Field("DEPENDENCY", description="关系类型")
    extend_props: dict[str, Any] = Field(
        default_factory=dict, description="关系扩展属性"
    )


class TaskExecutionGraphDTO(BaseModel):
    """任务执行图(图级运行态 + 节点表 + 分解树边 + 审计 DAG)。"""

    run_id: int = Field(..., description="图运行实例ID")
    loop_round: int = Field(..., description="当前 loop 轮次")
    status: str = Field(..., description="图状态(product 态)")
    output: dict[str, Any] = Field(default_factory=dict, description="图级输出")
    tasks: list[TaskNodeDTO] = Field(default_factory=list, description="分解树节点列表")
    relations: list[TaskRelationDTO] = Field(
        default_factory=list, description="依赖关系(分解树)"
    )
    extend_props: dict[str, Any] = Field(
        default_factory=dict, description="图级扩展属性"
    )
    execution_graph: dict[str, Any] | None = Field(
        None,
        description="回调审计 DAG 快照(按 root session_id 从 task_callback 反查挂图级)",
    )
    execution_config: dict[str, Any] = Field(
        default_factory=dict,
        description="执行配置投影(task_type/yaml/workflow_id + 会话/群/父任务上下文扁平,扩展上下文兼容归一)",
    )


def runtime_status_to_product_status(status: Any) -> str:
    """Map runtime task states to product-facing dashboard states.

    ``PENDING`` is exposed as product ``DEFINED``. ``DRAFTING`` remains an
    authoring-layer state and is not produced by the runtime dashboard.
    Runtime ``HUNG`` is exposed as ``REVIEWING``; other active runtime states
    are ``EXECUTING``.
    """
    value = status.value if hasattr(status, "value") else str(status)
    return {
        "PENDING": "DEFINED",
        "HUNG": "REVIEWING",
        "DONE": "DONE",
        "SUCCESS": "SUCCESS",
        "FAILED": "FAILED",
        "CANCELLED": "CANCELLED",
    }.get(value, "EXECUTING")


def execution_graph_to_product_status(execution_graph: Any) -> Any:
    """Return an execution-graph response copy with product-facing statuses.

    ``execution_graph`` is a third-party execution snapshot stored as a plain
    dictionary, so it does not pass through :func:`graph_to_dto`'s typed graph
    conversion. Normalize only the graph-level ``status`` and each direct
    task's ``status`` at the HTTP response boundary, leaving the stored
    execution snapshot and nested metadata untouched.
    """
    if not isinstance(execution_graph, dict):
        return execution_graph

    normalized = dict(execution_graph)
    if "status" in normalized:
        normalized["status"] = runtime_status_to_product_status(normalized["status"])

    raw_tasks = normalized.get("tasks")
    if isinstance(raw_tasks, list):
        normalized["tasks"] = [
            (
                {
                    **task,
                    "status": runtime_status_to_product_status(task["status"]),
                }
                if isinstance(task, dict) and "status" in task
                else task
            )
            for task in raw_tasks
        ]
    return normalized


# ===== DTO <-> domain conversion(Rule 22:adapter 唯一写/读翻译位) =====


def task_spec_from_dto(dto: TaskSpecDTO):
    """TaskSpecDTO → domain TaskSpec(Rule 22:adapter 唯一写翻译位;task_info_from_dto / bbs_attach 复用)。"""
    from agentclaw.community.core.task.domain.models import (
        AcceptanceCriteria,
        Context,
        Goal,
        Metadata,
        TaskSpec,
    )

    return TaskSpec(
        metadata=Metadata(
            task_id=dto.metadata.task_id,
            title=dto.metadata.title,
            instruction=dto.metadata.instruction,
        ),
        context=Context(
            background=dto.context.background,
            extend_props=dict(dto.context.extend_props),
        ),
        goal=Goal(
            objective=dto.goal.objective,
            acceptances=[
                AcceptanceCriteria(id=a.id, description=a.description)
                for a in dto.goal.acceptances
            ],
        ),
    )


def task_info_request_from_dto(dto: TaskInfoRequestDTO):
    """TaskInfoRequestDTO → domain TaskInfoRequest(Rule 22:adapter 唯一写翻译位)。"""
    from agentclaw.community.core.task.domain.models import TaskSourceType, TaskType
    from agentclaw.community.core.task.domain.requests import (
        RequestAcceptance,
        RequestContext,
        RequestGoal,
        RequestMetadata,
        RequestTaskSpec,
        TaskInfoRequest,
    )

    ec = dto.execution_config
    execution_config: dict[str, Any] = dict(ec.model_dump(exclude_none=True))
    execution_config["task_type"] = TaskType(ec.task_type)
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(
                title=dto.task_spec.metadata.title,
                instruction=dto.task_spec.metadata.instruction,
            ),
            context=RequestContext(
                background=dto.task_spec.context.background,
                extend_props=dict(dto.task_spec.context.extend_props),
            ),
            goal=RequestGoal(
                objective=dto.task_spec.goal.objective,
                acceptances=[
                    RequestAcceptance(id=a.id, acceptance=a.acceptance)
                    for a in dto.task_spec.goal.acceptances
                ],
            ),
        ),
        source_type=TaskSourceType(dto.source_type),
        owner_user_id=dto.owner_user_id,
        owner_bot_id=dto.owner_bot_id,
        execution_config=execution_config,
    )


def callback_from_dto(dto: TaskCallbackDataDTO):
    from agentclaw.community.core.task.domain.models import TaskCallbackData

    result = dict(dto.result)
    # Legacy report callers may send the envelope-shaped {code, data};
    # normalize it to the callback adapter's {success, data} contract.
    if "success" not in result and "exec_error" not in result:
        result = {
            "success": result.get("code", 200000) == 200000,
            **({"data": result["data"]} if "data" in result else {}),
        }
    return TaskCallbackData(
        data={
            "loop_task_id": dto.loop_task_id,
            "workflow_type": dto.workflow_type,
            "workflow_id": dto.workflow_id,
            "instance_id": dto.instance_id,
            "result": result,
        }
    )


def acceptance_result_from_dto(dto: AcceptanceResultDTO):
    """AcceptanceResultDTO → domain AcceptanceResult(Rule 22:adapter 唯一写翻译位;bbs/result 路由复用)。"""
    from agentclaw.community.core.task.domain.models import (
        AcceptanceResult,
        AcceptanceVerdict,
    )

    return AcceptanceResult(
        verdict=AcceptanceVerdict(dto.verdict),
        acceptances_metric=list(dto.acceptances_metric),
        gaps=list(dto.gaps),
    )


def _normalize_execution_config(graph) -> dict[str, Any]:
    """Dashboard ``execution_config`` 顶层投影(统一新规范)。

    优先取 ``graph.extend_props["execution_config"]``(新建图时由 task_graph_service 写入);
    历史记录若该处缺会话/群/父任务 4 字段、但根节点 ``task_spec.context.extend_props.teamclaw_context``
    保留旧值,则只读回填进响应 ``execution_config``(不改存储,兼容归一)。``task_type`` 枚举转 value。
    """
    raw = graph.extend_props.get("execution_config") or {}
    ec: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    root = next(
        (n for n in graph.tasks if n.node_id == getattr(graph, "task_id", "")),
        None,
    )
    if root is None and graph.tasks:
        root = graph.tasks[0]
    if root is not None:
        tc = (root.task_spec.context.extend_props or {}).get("teamclaw_context") or {}
        if isinstance(tc, dict):
            for key in ("main_session_id", "main_session_name", "source_group_id", "parent_task_id"):
                # key in tc 而非 value 非空:历史 tc.parent_task_id 显式 None 同样回填,确保归一后繁键一致。
                if key not in ec and key in tc:
                    ec[key] = tc[key]
    _tt = ec.get("task_type")
    if hasattr(_tt, "value"):  # TaskType 枚举转字符串值,便于前端消费
        ec["task_type"] = _tt.value
    return ec


def _unwrap_node_output(d: Any) -> Any:
    """展平 adapter 落库的 ``{"output": <content>}`` 单键 dict 为标量内容。

    pull(poller)与 push(callback/report)两条链路统一按 callback/report 协议把产出挂在
    ``run_info.output["output"]``,与字典字段名 ``output`` 同名会形成 dashboard
    ``{output: {output: <content>}}`` 两层嵌套;此处仅在 DTO 出口展平为 ``output: <content>``,
    内部 sibling/child output、static/bbs/notify 等多键分支原样保留 dict。"""
    if isinstance(d, dict) and len(d) == 1 and "output" in d:
        return d["output"]
    return dict(d) if isinstance(d, dict) else d


# 内部字段不直接透出到 dashboard DTO。编排核仍从 graph.extend_props 读取
# execution_config 做运行时策略配置,但对外有唯一的顶层 execution_config 投影,避免重复返回。
_INTERNAL_GRAPH_EXT_PROPS = frozenset({"execution_config"})
# 内部飞行态标志(纯在途去重/陈旧判定),无 dashboard 价值且恒为 null 噪音 → 不透出到外部 DTO。
_INTERNAL_NODE_EXT_PROPS = frozenset({"dispatching", "dispatching_at"})


def graph_to_dto(graph, *, include_action_log: bool = False) -> TaskExecutionGraphDTO:
    nodes: list[TaskNodeDTO] = []
    for n in graph.tasks:
        ar = n.run_info.acceptance_result
        ar_dto = (
            AcceptanceResultDTO(
                verdict=ar.verdict.value,
                acceptances_metric=list(ar.acceptances_metric),
                gaps=list(ar.gaps),
            )
            if ar is not None
            else None
        )
        nodes.append(
            TaskNodeDTO(
                node_id=n.node_id,
                task_id=n.task_id,
                status=runtime_status_to_product_status(n.status),
                task_spec=TaskSpecDTO(
                    metadata=MetadataDTO(
                        task_id=n.task_spec.metadata.task_id,
                        title=n.task_spec.metadata.title,
                        instruction=n.task_spec.metadata.instruction,
                    ),
                    context=ContextDTO(
                        background=n.task_spec.context.background,
                        extend_props=dict(n.task_spec.context.extend_props),
                    ),
                    goal=GoalDTO(
                        objective=n.task_spec.goal.objective,
                        acceptances=[
                            AcceptanceCriteriaDTO(id=a.id, description=a.description)
                            for a in n.task_spec.goal.acceptances
                        ],
                    ),
                ),
                run_info=RuntimeInfoDTO(
                    run_mode=n.run_info.run_mode,
                    assignee=n.run_info.assignee,
                    start_time=n.run_info.start_time,
                    end_time=n.run_info.end_time,
                    output=_unwrap_node_output(n.run_info.output),
                    acceptance_result=ar_dto,
                    extend_props={
                        k: v
                        for k, v in n.run_info.extend_props.items()
                        if k not in _INTERNAL_NODE_EXT_PROPS
                    },
                    action_log=(
                        [
                            NodeActionEventDTO(
                                seq=e.seq,
                                ts=e.ts,
                                action=e.action.value,
                                loop_round=e.loop_round,
                                attempt=e.attempt,
                                status_from=e.status_from.value
                                if e.status_from
                                else None,
                                status_to=e.status_to.value if e.status_to else None,
                                payload=dict(e.payload),
                            )
                            for e in n.run_info.action_log
                        ]
                        if include_action_log
                        else []
                    ),
                ),
            )
        )
    relations = [
        TaskRelationDTO(
            src_id=r.src_id,
            dst_id=r.dst_id,
            type=r.type.value,
            extend_props=dict(r.extend_props),
        )
        for r in graph.relations
    ]
    return TaskExecutionGraphDTO(
        run_id=graph.run_id,
        loop_round=graph.loop_round,
        status=runtime_status_to_product_status(graph.status),
        output=dict(graph.output),
        tasks=nodes,
        relations=relations,
        extend_props={
            key: value
            for key, value in graph.extend_props.items()
            if key not in _INTERNAL_GRAPH_EXT_PROPS
        },
        execution_graph=execution_graph_to_product_status(graph.execution_graph),
        execution_config=_normalize_execution_config(graph),
    )


def task_info_record_to_dto(record) -> TaskInfoRecordDTO:
    """TaskInfoRecord -> TaskInfoRecordDTO(Rule 22)。"""
    return TaskInfoRecordDTO(
        id=record.id,
        task_id=record.task_id,
        source_type=record.source_type,
        owner_user_id=record.owner_user_id,
        owner_user_name=getattr(record, "owner_user_name", None),
        owner_bot_id=record.owner_bot_id,
        owner_bot_name=getattr(record, "owner_bot_name", None),
        execution_config=(
            dict(record.execution_config)
            if record.execution_config is not None
            else None
        ),
        task_spec=dict(record.task_spec),
        status=runtime_status_to_product_status(record.status),
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
    )


def bbs_task_overview_to_dto(record) -> BbsTaskItemDTO:
    """BbsTaskOverviewRecord -> BbsTaskItemDTO(Rule 22):二次解析 task_spec/extend_props。"""
    task_spec = record.task_spec or {}
    metadata = task_spec.get("metadata") or {}
    goal = task_spec.get("goal") or {}
    extend_props = record.extend_props or {}
    return BbsTaskItemDTO(
        task_id=record.task_id,
        node_id=record.node_id,
        run_mode=record.run_mode,
        retry=record.retry,
        assignee_id=record.assignee_id,
        status=record.status.value if record.status is not None else None,
        acceptance_result=record.acceptance_result,
        extend_props=record.extend_props,
        relay_create_time=record.relay_create_time,
        relay_begin_time=record.relay_begin_time,
        relay_end_time=record.relay_end_time,
        task_spec=dict(task_spec),
        title=metadata.get("title"),
        goal=goal.get("objective"),
        acceptances=goal.get("acceptances"),
        assignee_name=extend_props.get("assignee_name"),
        publisher=record.publisher,
        publisher_name=record.publisher_name,
    )


def op_result_to_dto(result) -> TaskOpResultDTO:
    # TaskOpResult 持 error(失败原因),无 message 字段;将 error 透出到 DTO.message,
    # 否则 failure 时 HTTP 响应只剩 success=false、原因被吞掉,无法排查。
    return TaskOpResultDTO(
        task_id=result.task_id,
        success=result.success,
        run_id=result.run_id,
        message=getattr(result, "error", None),
        extend_props=dict(result.extend_props or {}),
    )


# ===== task_loop inbound callback schemas(PUSH 回调,对齐羽雀 TaskCallbackData/TaskNodeCallbackData)=====
# SSOT TaskCallbackData 保持精简(不扩);羽雀丰富字段在 translator 边缘折叠进 SSOT。
# 必填非可选(AGENTS.md):task_id/workflow_source/workflow_id/workflow_instance_id/status/is_success。
# None 仅契约态:goal/output/failed_info/ext_info/loop_task_id(回声字段,缺失走 registry)。


class TaskCallbackRequest(BaseModel):
    """task 级(workflow)回调载荷。"""

    task_id: str
    workflow_source: Literal["claw_mind", "bcn"]
    workflow_id: str
    workflow_instance_id: str
    goal: str | None = None
    status: str
    is_success: bool
    output: dict[str, Any] | None = None
    failed_info: str | None = None
    ext_info: dict[str, Any] | None = None
    loop_task_id: str | None = None  # 回声字段:派发期透传,引擎原样回带(可选)


class TaskNodeCallbackRequest(TaskCallbackRequest):
    """node 级回调载荷(node_id 即 Avernet 子节点 id,统一领域对象 1:1 映射)。"""

    node_id: str


# ===== 任务认领 Bot 授权(grant/revoke)DTO =====
# 对齐 api-contract §1:`bcs_bot_id` = real:entity(bot_id:owner_user_id);
# cookie/referer 取自请求头(schema 内不承载,router 注入),operator = 登录态用户(staffId)。
# stateless:api-key 服务端持有,不落本地表;/grant、/revoke 端点对外在 openapi v1 task router。


class TaskGrantRequestDTO(BaseModel):
    """前端开启「任务认领」grant 公共 api-key 给某 Bot 的请求体。"""

    model_config = ConfigDict(extra="forbid")

    bcs_bot_id: str = Field(
        ...,
        description="被授权 Bot的 real:entity(bot_id:owner_user_id,即 /mine 的 bot.id 原值);"
        "遗留无 ':' 由后端用登录态 operator 补 owner 段",
    )


class TaskGrantResultDTO(BaseModel):
    """grant 成功回包。"""

    bcs_bot_id: str = Field(..., description="被授权 Bot 的 real:entity")
    api_key_prefix: str = Field(..., description="授权所用 api-key 前缀(secbaas 主键)")
    grant_status: str = Field(..., description="授权状态(granted)")
    operator: str = Field(..., description="执行 grant 的用户 id")


class TaskRevokeRequestDTO(BaseModel):
    """关闭「任务认领」撤销授权的请求体。"""

    model_config = ConfigDict(extra="forbid")

    bcs_bot_id: str = Field(..., description="撤销授权 Bot 的 real:entity")


class TaskRevokeResultDTO(BaseModel):
    """revoke 成功回包。"""

    bcs_bot_id: str = Field(..., description="撤销授权 Bot 的 real:entity")
    grant_status: str = Field(..., description="授权状态(revoked)")


# ===== 通用任务开关 DTO =====


class TaskSettingRequestDTO(BaseModel):
    """设置一种任务开关。"""

    model_config = ConfigDict(extra="forbid")

    setting_type: Literal["claim_join_filter", "search_skill", "skill_report_enabled", "harness_poller"] = Field(
        ..., description="任务开关类型"
    )
    enabled: bool = Field(..., description="是否启用")


class TaskSettingStateDTO(BaseModel):
    """任务开关当前状态。"""

    setting_type: Literal["claim_join_filter", "search_skill", "skill_report_enabled", "harness_poller"] = Field(
        ..., description="任务开关类型"
    )
    enabled: bool = Field(..., description="当前开关状态")
    env: str = Field(..., description="生效环境(prod/pre/dev)")


class TaskClaimJoinFilterRequestDTO(BaseModel):
    """Legacy request DTO for the hidden compatibility route."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(..., description="是否启用 claim_join_filter")


class TaskClaimJoinFilterStateDTO(BaseModel):
    """Legacy response DTO for the hidden compatibility route."""

    enabled: bool = Field(..., description="当前开关状态")
    env: str = Field(..., description="生效环境(prod/pre/dev)")
